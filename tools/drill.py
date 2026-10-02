"""Kill-mid-sync drill: kill -9 an ingest at a known point, then prove nothing was lost.

python -m tools.drill                  every stage, in a throwaway temp dir; exit 1 on a failure
python -m tools.drill --stage mid_transaction --payload fixtures/health/batch_part1.json

The drill never touches the live db or archive: each stage gets its own temp directory, and a
child process posts the payload to the real /ingest/health route there. The child pauses at
the named stage and writes a marker file; the parent kills it only after the marker exists,
so the kill point is exact and not a timing race.

Stages:
  before_row       raw file written, no raw_archive row yet
  after_archive    raw_archive row written (parsed_ok = 0), nothing parsed
  mid_transaction  parsed rows written inside the open transaction, not committed
  after_commit     everything committed, WAL not checkpointed (kill -9 right after a push)

This is a process kill, not a power cut: the OS keeps running and its page cache reaches
the disk. Surviving a power cut rests on WAL mode plus fsync of the raw file and is not
exercised here.
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field, replace
from pathlib import Path

from app.config import REPO_ROOT, BackupConfig, Settings, StorageConfig
from app.config import load_settings as load_live_settings
from app.db import open_db
from tools.replay import checksum, snapshot

STAGES = ("before_row", "after_archive", "mid_transaction", "after_commit")
DEFAULT_PAYLOAD = REPO_ROOT / "fixtures" / "health" / "workouts_v2_overlap.json"
CHILD_TIMEOUT_S = 60.0
POLL_S = 0.01


@dataclass(frozen=True)
class DrillResult:
    stage: str
    integrity: str
    raw_on_disk: bool
    parsed_ok_after_kill: list[int]
    wal_bytes_after_kill: int
    checksum_after_kill: str
    repost_status: str
    final_checksum: str
    clean_checksum: str
    final_parsed_ok: list[int]
    failures: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures


def drill_settings(workdir: Path) -> Settings:
    """Settings whose every writable path lives under `workdir`; scheduler off, no token."""
    base = load_live_settings()
    return replace(
        base,
        storage=StorageConfig(db_path=workdir / "life.db", raw_dir=workdir / "raw"),
        claude_usage=replace(base.claude_usage, path=workdir / "claude_usage.json"),
        backup=BackupConfig(workdir / "backups", base.backup.time, base.backup.keep),
        scheduler=replace(base.scheduler, enabled=False),
        health_export_token="",
    )


def isolate_process_from_live_data(workdir: Path) -> None:
    """Importing app.main opens the configured db; point that at the drill's directory."""
    os.environ["LIFE_DB_PATH"] = str(workdir / "import-only.db")
    os.environ["LIFE_RAW_DIR"] = str(workdir / "import-only-raw")
    os.environ["LIFE_SCHEDULER_ENABLED"] = "0"


def post(settings: Settings, body: bytes) -> dict[str, object]:
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app(settings)) as client:
        resp = client.post(
            "/ingest/health", content=body, headers={"content-type": "application/json"}
        )
    return {"http": resp.status_code, **resp.json()}


def data_checksum(settings: Settings) -> str:
    conn = open_db(settings.storage.db_path)
    try:
        return checksum(snapshot(conn))
    finally:
        conn.close()


def clean_run_checksum(body: bytes, workdir: Path) -> str:
    settings = drill_settings(workdir)
    post(settings, body)
    return data_checksum(settings)


def _child(stage: str, payload: Path, marker: Path) -> int:
    """Runs in the subprocess. Posts the payload and blocks forever at `stage`."""
    from app.ingest import health

    workdir = Path(os.environ["LIFE_DRILL_DIR"])
    settings = drill_settings(workdir)

    def pause() -> None:
        marker.write_text(stage)
        threading.Event().wait()

    if stage == "before_row":
        real_write = health._write_durably

        def write_then_pause(*args: object, **kwargs: object) -> None:
            real_write(*args, **kwargs)
            pause()

        health._write_durably = write_then_pause
    elif stage == "after_archive":
        health.parse_payload = lambda *args, **kwargs: pause()
    elif stage == "mid_transaction":
        real_store = health.store_payload

        def store_then_pause(*args: object, **kwargs: object) -> object:
            real_store(*args, **kwargs)
            pause()

        health.store_payload = store_then_pause

    holder = open_db(settings.storage.db_path)
    result = post(settings, payload.read_bytes())
    if stage == "after_commit" and result.get("status") == "ok":
        holder.execute("SELECT COUNT(*) FROM raw_archive").fetchone()
        pause()
    return 3


def kill_at_stage(stage: str, payload: Path, workdir: Path) -> None:
    """Start the ingest in a subprocess and SIGKILL it once it reports reaching `stage`."""
    if stage not in STAGES:
        raise ValueError(f"unknown stage {stage!r}")
    workdir.mkdir(parents=True, exist_ok=True)
    marker = workdir / "reached"
    env = {
        **os.environ,
        "LIFE_DRILL_DIR": str(workdir),
        "HEALTH_EXPORT_TOKEN": "",
        "LIFE_SCHEDULER_ENABLED": "0",
        "LIFE_DB_PATH": str(workdir / "import-only.db"),
        "LIFE_RAW_DIR": str(workdir / "import-only-raw"),
    }
    proc = subprocess.Popen(
        [
            sys.executable,
            "-W",
            "ignore",
            "-m",
            "tools.drill",
            "--child",
            stage,
            "--payload",
            str(payload),
            "--marker",
            str(marker),
        ],
        cwd=REPO_ROOT,
        env=env,
    )
    deadline = time.monotonic() + CHILD_TIMEOUT_S
    try:
        while not marker.exists():
            try:
                code = proc.wait(timeout=POLL_S)
            except subprocess.TimeoutExpired:
                if time.monotonic() > deadline:
                    raise TimeoutError(f"child never reached {stage}") from None
                continue
            raise RuntimeError(f"child exited ({code}) before reaching {stage}")
    finally:
        if proc.poll() is None:
            proc.send_signal(signal.SIGKILL)
        proc.wait()


def _parsed_flags(settings: Settings) -> tuple[str, list[int]]:
    conn = open_db(settings.storage.db_path)
    try:
        state = str(conn.execute("PRAGMA integrity_check").fetchone()[0])
        rows = conn.execute("SELECT parsed_ok FROM raw_archive ORDER BY id").fetchall()
        return state, [int(r["parsed_ok"]) for r in rows]
    finally:
        conn.close()


def run_stage(stage: str, payload: Path, workdir: Path) -> DrillResult:
    """Kill an ingest at `stage`, re-post the same payload, and compare with a clean run."""
    body = payload.read_bytes()
    clean = clean_run_checksum(body, workdir / "clean")
    empty = data_checksum(drill_settings(workdir / "empty"))
    killed_dir = workdir / "killed"
    kill_at_stage(stage, payload, killed_dir)
    settings = drill_settings(killed_dir)

    wal = Path(str(settings.storage.db_path) + "-wal")
    wal_bytes = wal.stat().st_size if wal.exists() else 0
    raw_files = sorted((settings.storage.raw_dir / "health").glob("*.json"))
    raw_on_disk = any(f.read_bytes() == body for f in raw_files)
    integrity, flags_after_kill = _parsed_flags(settings)
    after_kill = data_checksum(settings)
    repost = post(settings, body)
    final = data_checksum(settings)
    _, final_flags = _parsed_flags(settings)

    failures: list[str] = []
    if integrity != "ok":
        failures.append(f"integrity_check: {integrity}")
    if not raw_on_disk:
        failures.append("raw payload is not on disk verbatim")
    committed = stage == "after_commit"
    if committed and after_kill != clean:
        failures.append("committed data lost: checksum after the kill differs from a clean run")
    if committed and wal_bytes == 0:
        failures.append("drill did not leave a WAL to recover from")
    if not committed and after_kill != empty:
        failures.append("partial rows survived the kill")
    expected_status = "duplicate" if committed else "ok"
    if repost.get("status") != expected_status:
        failures.append(f"re-post answered {repost.get('status')!r}, expected {expected_status!r}")
    if final != clean:
        failures.append("checksum after re-post differs from a clean run")
    if final_flags != [1]:
        failures.append(f"raw_archive parsed_ok after re-post is {final_flags}, expected [1]")
    return DrillResult(
        stage=stage,
        integrity=integrity,
        raw_on_disk=raw_on_disk,
        parsed_ok_after_kill=flags_after_kill,
        wal_bytes_after_kill=wal_bytes,
        checksum_after_kill=after_kill,
        repost_status=str(repost.get("status")),
        final_checksum=final,
        clean_checksum=clean,
        final_parsed_ok=final_flags,
        failures=failures,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="tools.drill", description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--stage", choices=STAGES, action="append")
    parser.add_argument("--payload", default=str(DEFAULT_PAYLOAD))
    parser.add_argument("--child", choices=STAGES, help=argparse.SUPPRESS)
    parser.add_argument("--marker", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    payload = Path(args.payload).resolve()

    if args.child:
        return _child(args.child, payload, Path(args.marker))

    failed = False
    with tempfile.TemporaryDirectory(prefix="life-drill-") as tmp:
        isolate_process_from_live_data(Path(tmp))
        for stage in args.stage or STAGES:
            result = run_stage(stage, payload, Path(tmp) / stage)
            failed = failed or not result.ok
            print(
                f"{stage:16s} {'PASS' if result.ok else 'FAIL'}  integrity={result.integrity} "
                f"raw_on_disk={result.raw_on_disk} parsed_ok_after_kill="
                f"{result.parsed_ok_after_kill} wal_bytes={result.wal_bytes_after_kill} "
                f"repost={result.repost_status} checksum={result.final_checksum[:12]} "
                f"clean={result.clean_checksum[:12]}"
            )
            for line in result.failures:
                print(f"  - {line}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
