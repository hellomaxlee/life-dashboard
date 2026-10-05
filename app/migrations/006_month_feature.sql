-- The month feature: one authored theme a month (app/month/). `feature_json` is the validated
-- object exactly as stored; `raw_reply` is the model's reply verbatim.
CREATE TABLE month_features (
    month_local TEXT PRIMARY KEY,
    feature_json TEXT NOT NULL,
    source TEXT NOT NULL,
    model TEXT,
    raw_reply TEXT,
    created_at_utc TEXT NOT NULL
);

-- One row per generation run that called the model and stored nothing. The scheduler's
-- guard reads it: at most one automatic run per local day and three per month.
CREATE TABLE month_feature_attempts (
    id INTEGER PRIMARY KEY,
    month_local TEXT NOT NULL,
    day_local TEXT NOT NULL,
    calls INTEGER NOT NULL,
    reason TEXT NOT NULL,
    created_at_utc TEXT NOT NULL
);

CREATE INDEX month_feature_attempts_month ON month_feature_attempts (month_local);
