-- The city panel (app/city/): the latest parsed transit snapshot and the latest parsed
-- weather snapshot, one row per kind, replaced on each successful fetch. Display-only: not
-- ingested data, not a metric, and not rebuilt by replay (the feeds are not archived).
CREATE TABLE city_snapshots (
    kind TEXT PRIMARY KEY CHECK (kind IN ('transit', 'weather')),
    snapshot_json TEXT NOT NULL,
    fetched_at_utc TEXT NOT NULL
);
