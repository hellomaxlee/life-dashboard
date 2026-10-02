from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = REPO_ROOT / "config.toml"


@dataclass(frozen=True)
class WorkoutConfig:
    load_bar: float
    recalibrate_months: int
    calibration_min_runs: int


@dataclass(frozen=True)
class WellnessBands:
    hrv_pct: float
    rhr_bpm: float
    daylight_min: float


@dataclass(frozen=True)
class WellnessConfig:
    baseline_days: int
    bands: WellnessBands


@dataclass(frozen=True)
class BooksConfig:
    target_per_year: int
    fallback_to_date_added: bool


@dataclass(frozen=True)
class ClaudeUsageConfig:
    path: Path
    stale_hours: int


@dataclass(frozen=True)
class PullConfig:
    health_interval_hours: int
    goodreads: str


@dataclass(frozen=True)
class ServerConfig:
    host: str
    port: int


@dataclass(frozen=True)
class StorageConfig:
    db_path: Path
    raw_dir: Path


@dataclass(frozen=True)
class DedupeConfig:
    start_window_min: float
    duration_tolerance_pct: float


@dataclass(frozen=True)
class IngestConfig:
    hr_incomplete_ratio: float
    dedupe: DedupeConfig


@dataclass(frozen=True)
class SummaryConfig:
    model: str
    monthly_cap_usd: float


@dataclass(frozen=True)
class SchedulerConfig:
    enabled: bool
    usage_poll_seconds: int
    usage_min_read_seconds: int


@dataclass(frozen=True)
class BackupConfig:
    dir: Path
    time: str
    keep: int


@dataclass(frozen=True)
class DeviceConfig:
    pixoo_host: str = ""
    screen_seconds: int = 20


@dataclass(frozen=True)
class Settings:
    home_tz: str
    hr_max: int
    zones_pct: tuple[float, ...]
    workout: WorkoutConfig
    week_target: int
    sleep_target_hours: float
    books: BooksConfig
    wellness: WellnessConfig
    claude_usage: ClaudeUsageConfig
    pull: PullConfig
    server: ServerConfig
    storage: StorageConfig
    ingest: IngestConfig
    summary: SummaryConfig
    health_export_token: str
    goodreads_rss_url: str
    scheduler: SchedulerConfig
    backup: BackupConfig
    device: DeviceConfig = DeviceConfig()


def _resolve(path_str: str) -> Path:
    path = Path(path_str)
    return path if path.is_absolute() else REPO_ROOT / path


_ON = {"1", "true", "yes"}
_OFF = {"0", "false", "no"}


def _env_switch(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    word = value.strip().lower()
    if word not in _ON | _OFF:
        raise ValueError(f"{name} must be one of 1/true/yes or 0/false/no, not {value!r}")
    return word in _ON


def _fail(key: str, value: object, want: str) -> ValueError:
    return ValueError(f"config {key} must be {want}, not {value!r}")


def _whole(key: str, value: object, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise _fail(key, value, f"a whole number of at least {minimum}")
    return value


def _number(key: str, value: object, minimum: float, maximum: float | None = None) -> float:
    ok = isinstance(value, int | float) and not isinstance(value, bool) and value >= minimum
    if not ok or (maximum is not None and value > maximum):
        top = "" if maximum is None else f" and at most {maximum}"
        raise _fail(key, value, f"a number of at least {minimum}{top}")
    return float(value)


def _flag(key: str, value: object) -> bool:
    if not isinstance(value, bool):
        raise _fail(key, value, "true or false (no quotes)")
    return value


def _clock(key: str, value: object) -> str:
    parts = value.split(":") if isinstance(value, str) else []
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        raise _fail(key, value, "a time written HH:MM")
    if int(parts[0]) > 23 or int(parts[1]) > 59:
        raise _fail(key, value, "a time written HH:MM")
    return value


def _timezone(key: str, value: object) -> str:
    try:
        ZoneInfo(str(value))
    except Exception as exc:
        raise _fail(key, value, "an IANA timezone name") from exc
    return str(value)


def _folder(key: str, value: object) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise _fail(key, value, "a directory path")
    return _resolve(value)


def load_settings(config_path: Path | None = None) -> Settings:
    """Load config.toml into a frozen Settings.

    Environment overrides: LIFE_CONFIG_PATH (file), LIFE_DB_PATH, LIFE_RAW_DIR (storage),
    LIFE_BACKUP_DIR, LIFE_SCHEDULER_ENABLED (1/true/yes or 0/false/no),
    HEALTH_EXPORT_TOKEN and GOODREADS_RSS_URL (secrets, from .env or the environment).
    """
    load_dotenv(REPO_ROOT / ".env")
    path = config_path or Path(os.environ.get("LIFE_CONFIG_PATH", DEFAULT_CONFIG_PATH))
    with path.open("rb") as fh:
        raw = tomllib.load(fh)

    dedupe = raw["ingest"]["dedupe"]
    bands = raw["wellness"]["bands"]
    return Settings(
        home_tz=_timezone("home_tz", raw["home_tz"]),
        hr_max=int(raw["hr_max"]),
        zones_pct=tuple(float(p) for p in raw["zones"]["pct"]),
        workout=WorkoutConfig(
            load_bar=float(raw["workout"]["load_bar"]),
            recalibrate_months=int(raw["workout"]["recalibrate_months"]),
            calibration_min_runs=int(raw["workout"]["calibration_min_runs"]),
        ),
        week_target=int(raw["week"]["target"]),
        sleep_target_hours=float(raw["sleep"]["target_hours"]),
        books=BooksConfig(
            target_per_year=int(raw["books"]["target_per_year"]),
            fallback_to_date_added=bool(raw["books"]["fallback_to_date_added"]),
        ),
        wellness=WellnessConfig(
            baseline_days=int(raw["wellness"]["baseline_days"]),
            bands=WellnessBands(
                hrv_pct=float(bands["hrv_pct"]),
                rhr_bpm=float(bands["rhr_bpm"]),
                daylight_min=float(bands["daylight_min"]),
            ),
        ),
        claude_usage=ClaudeUsageConfig(
            path=_resolve(raw["claude_usage"]["path"]),
            stale_hours=int(raw["claude_usage"]["stale_hours"]),
        ),
        pull=PullConfig(
            health_interval_hours=int(raw["pull"]["health_interval_hours"]),
            goodreads=str(raw["pull"]["goodreads"]),
        ),
        server=ServerConfig(host=raw["server"]["host"], port=int(raw["server"]["port"])),
        storage=StorageConfig(
            db_path=_resolve(os.environ.get("LIFE_DB_PATH", raw["storage"]["db_path"])),
            raw_dir=_resolve(os.environ.get("LIFE_RAW_DIR", raw["storage"]["raw_dir"])),
        ),
        ingest=IngestConfig(
            hr_incomplete_ratio=_number(
                "ingest.hr_incomplete_ratio", raw["ingest"]["hr_incomplete_ratio"], 0, 1
            ),
            dedupe=DedupeConfig(
                start_window_min=_number(
                    "ingest.dedupe.start_window_min", dedupe["start_window_min"], 0
                ),
                duration_tolerance_pct=_number(
                    "ingest.dedupe.duration_tolerance_pct", dedupe["duration_tolerance_pct"], 0
                ),
            ),
        ),
        summary=SummaryConfig(
            model=str(raw["summary"]["model"]),
            monthly_cap_usd=float(raw["summary"]["monthly_cap_usd"]),
        ),
        health_export_token=os.environ.get("HEALTH_EXPORT_TOKEN", "").strip(),
        goodreads_rss_url=os.environ.get("GOODREADS_RSS_URL", "").strip(),
        scheduler=SchedulerConfig(
            enabled=_env_switch(
                "LIFE_SCHEDULER_ENABLED", _flag("scheduler.enabled", raw["scheduler"]["enabled"])
            ),
            usage_poll_seconds=_whole(
                "scheduler.usage_poll_seconds", raw["scheduler"]["usage_poll_seconds"], 1
            ),
            usage_min_read_seconds=_whole(
                "scheduler.usage_min_read_seconds", raw["scheduler"]["usage_min_read_seconds"], 0
            ),
        ),
        backup=BackupConfig(
            dir=_folder("backup.dir", os.environ.get("LIFE_BACKUP_DIR", raw["backup"]["dir"])),
            time=_clock("backup.time", raw["backup"]["time"]),
            keep=_whole("backup.keep", raw["backup"]["keep"], 1),
        ),
        device=DeviceConfig(
            pixoo_host=str(raw.get("device", {}).get("pixoo_host", "")).strip(),
            screen_seconds=int(raw.get("device", {}).get("screen_seconds", 20)),
        ),
    )
