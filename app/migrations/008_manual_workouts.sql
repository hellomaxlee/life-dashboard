-- Manual workout overrides (app/metrics/manual.py): Max's own word that a day held a quality
-- workout when the feed could not say so. One row per home-timezone day; adding again
-- replaces the note. Authored data: not rebuilt by replay, kept by --rebuild-live, carried
-- by backup, and read by the metrics engine like any other input.
CREATE TABLE manual_workouts (
    day_local TEXT PRIMARY KEY,
    note TEXT NOT NULL DEFAULT '',
    created_at_utc TEXT NOT NULL
);
