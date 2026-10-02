from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

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
class DeviceConfig:
    pixoo_host: str = ""


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
    device: DeviceConfig = DeviceConfig()


def _resolve(path_str: str) -> Path:
    path = Path(path_str)
    return path if path.is_absolute() else REPO_ROOT / path


def load_settings(config_path: Path | None = None) -> Settings:
    """Load config.toml into a frozen Settings.

    Environment overrides: LIFE_CONFIG_PATH (file), LIFE_DB_PATH, LIFE_RAW_DIR (storage),
    HEALTH_EXPORT_TOKEN (secret, from .env or the environment).
    """
    load_dotenv(REPO_ROOT / ".env")
    path = config_path or Path(os.environ.get("LIFE_CONFIG_PATH", DEFAULT_CONFIG_PATH))
    with path.open("rb") as fh:
        raw = tomllib.load(fh)

    dedupe = raw["ingest"]["dedupe"]
    bands = raw["wellness"]["bands"]
    return Settings(
        home_tz=raw["home_tz"],
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
            hr_incomplete_ratio=float(raw["ingest"]["hr_incomplete_ratio"]),
            dedupe=DedupeConfig(
                start_window_min=float(dedupe["start_window_min"]),
                duration_tolerance_pct=float(dedupe["duration_tolerance_pct"]),
            ),
        ),
        summary=SummaryConfig(
            model=str(raw["summary"]["model"]),
            monthly_cap_usd=float(raw["summary"]["monthly_cap_usd"]),
        ),
        health_export_token=os.environ.get("HEALTH_EXPORT_TOKEN", "").strip(),
        device=DeviceConfig(pixoo_host=str(raw.get("device", {}).get("pixoo_host", "")).strip()),
    )
