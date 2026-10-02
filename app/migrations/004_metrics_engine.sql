-- Every change to the load bar, with the runs that decided it. A bar applies from
-- effective_from_week (a Monday) forward; earlier weeks keep the bar they were scored under.
CREATE TABLE load_bar_history (
    effective_from_week TEXT PRIMARY KEY,
    value REAL NOT NULL,
    source_ids_json TEXT NOT NULL DEFAULT '[]',
    decided_at_utc TEXT NOT NULL
);

-- What the last metrics recompute saw as "today" and "now", so a replay can recompute the
-- derived rows under the same clock and compare them with live.
CREATE TABLE metrics_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
