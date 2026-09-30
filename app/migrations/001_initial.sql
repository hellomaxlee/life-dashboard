CREATE TABLE raw_archive (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,
    received_at_utc TEXT NOT NULL,
    sha256 TEXT NOT NULL UNIQUE,
    path TEXT NOT NULL,
    byte_len INTEGER NOT NULL,
    parsed_ok INTEGER NOT NULL DEFAULT 0,
    error TEXT
);

CREATE TABLE activities (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    start_utc TEXT NOT NULL,
    end_utc TEXT NOT NULL,
    duration_s INTEGER NOT NULL,
    distance_m REAL,
    energy_kcal REAL,
    avg_hr REAL,
    max_hr REAL,
    hr_sample_count INTEGER NOT NULL DEFAULT 0,
    hr_span_s INTEGER NOT NULL DEFAULT 0,
    hr_incomplete INTEGER NOT NULL DEFAULT 1,
    merged_from_json TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX activities_start_utc ON activities (start_utc);

CREATE TABLE activity_sources (
    activity_id TEXT NOT NULL REFERENCES activities (id) ON DELETE CASCADE,
    source_app TEXT NOT NULL,
    external_id TEXT NOT NULL,
    start_utc TEXT NOT NULL,
    end_utc TEXT NOT NULL,
    raw_archive_id INTEGER REFERENCES raw_archive (id),
    PRIMARY KEY (external_id, source_app)
);
CREATE INDEX activity_sources_activity_id ON activity_sources (activity_id);

CREATE TABLE workout_hr_samples (
    activity_id TEXT NOT NULL REFERENCES activities (id) ON DELETE CASCADE,
    ts_utc TEXT NOT NULL,
    bpm_min REAL,
    bpm_avg REAL,
    bpm_max REAL,
    source TEXT NOT NULL,
    PRIMARY KEY (activity_id, ts_utc, source)
);

CREATE TABLE sleep_sessions (
    id TEXT PRIMARY KEY,
    wake_day_local TEXT NOT NULL,
    start_utc TEXT NOT NULL,
    end_utc TEXT NOT NULL,
    in_bed_s INTEGER,
    asleep_s INTEGER,
    core_s INTEGER,
    deep_s INTEGER,
    rem_s INTEGER,
    awake_s INTEGER,
    source TEXT NOT NULL,
    UNIQUE (start_utc, end_utc, source)
);
CREATE INDEX sleep_sessions_wake_day ON sleep_sessions (wake_day_local);

CREATE TABLE steps_daily (
    day_local TEXT PRIMARY KEY,
    steps INTEGER NOT NULL,
    source TEXT NOT NULL
);

CREATE TABLE wellness_daily (
    day_local TEXT NOT NULL,
    metric TEXT NOT NULL,
    value REAL NOT NULL,
    units TEXT,
    source TEXT NOT NULL,
    PRIMARY KEY (day_local, metric, source)
);

CREATE TABLE daily_metrics (
    day_local TEXT PRIMARY KEY,
    metrics_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE weekly_metrics (
    week_start_local TEXT PRIMARY KEY,
    metrics_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE books (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    author TEXT,
    read_at TEXT,
    date_added TEXT,
    date_inferred INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE ingest_log (
    id INTEGER PRIMARY KEY,
    raw_archive_id INTEGER NOT NULL REFERENCES raw_archive (id),
    workouts_seen INTEGER NOT NULL DEFAULT 0,
    workouts_merged INTEGER NOT NULL DEFAULT 0,
    metrics_rows INTEGER NOT NULL DEFAULT 0,
    unknown_metrics TEXT NOT NULL DEFAULT '[]'
);
