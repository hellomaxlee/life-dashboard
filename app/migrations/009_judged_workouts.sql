-- Judged workouts (app/metrics/judge.py): a model's verdict that a day's heart-rate
-- aggregates held a quality workout when no scored activity or manual override did.
-- One row per home-timezone day; `verdict` is 'yes', 'no' or 'denied' (Max removed the
-- credit on the LAN page; the row keeps its inputs_hash so the day is never re-judged
-- while its inputs stand). Authored data: not rebuilt by replay, kept by --rebuild-live,
-- carried by backup, read by the metrics engine like manual_workouts.
CREATE TABLE judged_workouts (
    day_local TEXT PRIMARY KEY,
    verdict TEXT NOT NULL CHECK (verdict IN ('yes', 'no', 'denied')),
    confidence REAL NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL,
    inputs_hash TEXT NOT NULL,
    created_at_utc TEXT NOT NULL
);
