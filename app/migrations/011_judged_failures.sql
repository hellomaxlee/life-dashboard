-- Judged workouts keep every outcome (app/metrics/judge.py): a judgement the gate or the
-- parser rejected, or that the model answered with the wrong stop reason or an API error,
-- is stored as verdict 'failed' with its inputs_hash and the failure in `error`, so the
-- day is not sent again until its inputs change. SQLite cannot widen a CHECK in place,
-- so the table is rebuilt with its rows carried over.
CREATE TABLE judged_workouts_new (
    day_local TEXT PRIMARY KEY,
    verdict TEXT NOT NULL CHECK (verdict IN ('yes', 'no', 'denied', 'failed')),
    confidence REAL NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL,
    inputs_hash TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    error TEXT NOT NULL DEFAULT ''
);
INSERT INTO judged_workouts_new (day_local, verdict, confidence, reason, model, inputs_hash,
    created_at_utc)
    SELECT day_local, verdict, confidence, reason, model, inputs_hash, created_at_utc
    FROM judged_workouts;
DROP TABLE judged_workouts;
ALTER TABLE judged_workouts_new RENAME TO judged_workouts;
