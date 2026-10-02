-- Phase 3: the daily summary's memory and the model spend counter.
CREATE TABLE summary_lines (
    day_local TEXT PRIMARY KEY,
    line TEXT NOT NULL,
    web_line TEXT,
    lens TEXT NOT NULL,
    source TEXT NOT NULL,
    gate_result TEXT NOT NULL,
    attempts_json TEXT NOT NULL DEFAULT '[]',
    written_at_utc TEXT NOT NULL
);

CREATE TABLE model_spend (
    id INTEGER PRIMARY KEY,
    day_local TEXT NOT NULL,
    month_local TEXT NOT NULL,
    request_id TEXT,
    model TEXT NOT NULL,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens INTEGER NOT NULL DEFAULT 0,
    cache_creation_tokens INTEGER NOT NULL DEFAULT 0,
    usd REAL NOT NULL,
    stop_reason TEXT,
    created_at_utc TEXT NOT NULL
);
CREATE INDEX model_spend_month ON model_spend (month_local);
