-- Heart rate at minute resolution, when Health Auto Export aggregates by minute. Each row
-- is one minute's Min/Avg/Max; the day's whole-day figures still land in wellness_daily.
-- The judge reads these to form bouts; nothing here ever leaves the LAN.
CREATE TABLE hr_minutes (
    day_local TEXT NOT NULL,
    minute_utc TEXT PRIMARY KEY,
    hr_min REAL,
    hr_avg REAL,
    hr_max REAL
);
CREATE INDEX hr_minutes_day ON hr_minutes (day_local);
