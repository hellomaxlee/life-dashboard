-- Wellness: one value per (day, metric); source becomes a plain column so a relabelled
-- source replaces the value instead of adding a second one. Of several existing rows for a
-- day and metric, the one written last (highest rowid) is kept.
CREATE TABLE wellness_daily_v2 (
    day_local TEXT NOT NULL,
    metric TEXT NOT NULL,
    value REAL NOT NULL,
    units TEXT,
    source TEXT NOT NULL,
    PRIMARY KEY (day_local, metric)
);
INSERT INTO wellness_daily_v2 (day_local, metric, value, units, source)
SELECT day_local, metric, value, units, source FROM wellness_daily
WHERE rowid IN (SELECT MAX(rowid) FROM wellness_daily GROUP BY day_local, metric)
ORDER BY rowid;
DROP TABLE wellness_daily;
ALTER TABLE wellness_daily_v2 RENAME TO wellness_daily;

-- Sleep: a night reported twice with different times left two rows. Where two sessions of
-- the same wake day and source overlap in time, the one written last is kept. Sessions that
-- do not overlap (a nap and a night) are both kept.
DELETE FROM sleep_sessions
WHERE EXISTS (
    SELECT 1 FROM sleep_sessions AS later
    WHERE later.wake_day_local = sleep_sessions.wake_day_local
      AND later.source = sleep_sessions.source
      AND later.rowid > sleep_sessions.rowid
      AND later.start_utc < sleep_sessions.end_utc
      AND sleep_sessions.start_utc < later.end_utc
);
