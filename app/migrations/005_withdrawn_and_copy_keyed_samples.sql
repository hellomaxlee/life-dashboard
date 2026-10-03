-- Issue #4: clustering owns activities. Every copy keeps its own provenance, a copy or an
-- activity that Health no longer reports is marked withdrawn (never deleted), and heart-rate
-- samples belong to the copy that carried them so a cluster can split or re-key without
-- losing or double-counting them. `tools.migrate` re-clusters every stored activity after
-- this file runs.
ALTER TABLE activities ADD COLUMN withdrawn_at TEXT;

CREATE TABLE activity_sources_v2 (
    external_id TEXT NOT NULL,
    source_app TEXT NOT NULL,
    activity_id TEXT NOT NULL REFERENCES activities (id) ON DELETE CASCADE,
    start_utc TEXT NOT NULL,
    end_utc TEXT NOT NULL,
    raw_archive_id INTEGER REFERENCES raw_archive (id),
    withdrawn_at TEXT,
    provenance_json TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (external_id, source_app)
);
INSERT INTO activity_sources_v2
    (external_id, source_app, activity_id, start_utc, end_utc, raw_archive_id, provenance_json)
SELECT c.external_id, c.source_app, c.activity_id, c.start_utc, c.end_utc, c.raw_archive_id,
       COALESCE(
           (SELECT j.value FROM activities a, json_each(a.merged_from_json) j
            WHERE a.id = c.activity_id
              AND json_extract(j.value, '$.external_id') = c.external_id
              AND json_extract(j.value, '$.source_app') = c.source_app),
           '{}')
FROM activity_sources c;
DROP TABLE activity_sources;
ALTER TABLE activity_sources_v2 RENAME TO activity_sources;
CREATE INDEX activity_sources_activity_id ON activity_sources (activity_id);
CREATE INDEX activity_sources_start_utc ON activity_sources (start_utc);

-- A sample belongs to the copy whose source app is the sample's source; failing that, to the
-- activity's canonical copy (first in merged_from_json); failing that, to the activity's id
-- under the 'unknown' source.
CREATE TABLE workout_hr_samples_v2 (
    external_id TEXT NOT NULL,
    source_app TEXT NOT NULL,
    ts_utc TEXT NOT NULL,
    bpm_min REAL,
    bpm_avg REAL,
    bpm_max REAL,
    source TEXT NOT NULL,
    PRIMARY KEY (external_id, source_app, ts_utc, source),
    FOREIGN KEY (external_id, source_app)
        REFERENCES activity_sources (external_id, source_app) ON DELETE CASCADE
);
INSERT OR IGNORE INTO workout_hr_samples_v2
    (external_id, source_app, ts_utc, bpm_min, bpm_avg, bpm_max, source)
SELECT COALESCE(
           (SELECT c.external_id FROM activity_sources c
            WHERE c.activity_id = s.activity_id AND c.source_app = s.source
            ORDER BY c.external_id LIMIT 1),
           json_extract(a.merged_from_json, '$[0].external_id'),
           s.activity_id),
       COALESCE(
           (SELECT c.source_app FROM activity_sources c
            WHERE c.activity_id = s.activity_id AND c.source_app = s.source
            ORDER BY c.external_id LIMIT 1),
           json_extract(a.merged_from_json, '$[0].source_app'),
           'unknown'),
       s.ts_utc, s.bpm_min, s.bpm_avg, s.bpm_max, s.source
FROM workout_hr_samples s JOIN activities a ON a.id = s.activity_id
WHERE EXISTS (
    SELECT 1 FROM activity_sources c
    WHERE c.external_id = COALESCE(
              (SELECT c2.external_id FROM activity_sources c2
               WHERE c2.activity_id = s.activity_id AND c2.source_app = s.source
               ORDER BY c2.external_id LIMIT 1),
              json_extract(a.merged_from_json, '$[0].external_id'),
              s.activity_id)
      AND c.source_app = COALESCE(
              (SELECT c2.source_app FROM activity_sources c2
               WHERE c2.activity_id = s.activity_id AND c2.source_app = s.source
               ORDER BY c2.external_id LIMIT 1),
              json_extract(a.merged_from_json, '$[0].source_app'),
              'unknown')
);
DROP TABLE workout_hr_samples;
ALTER TABLE workout_hr_samples_v2 RENAME TO workout_hr_samples;

-- Sleep: one person, one night. Where two stored sessions under different source labels
-- overlap by at least half of the shorter one, the one written last (highest rowid) is kept.
DELETE FROM sleep_sessions
WHERE EXISTS (
    SELECT 1 FROM sleep_sessions AS later
    WHERE later.source <> sleep_sessions.source
      AND later.rowid > sleep_sessions.rowid
      AND later.start_utc < sleep_sessions.end_utc
      AND sleep_sessions.start_utc < later.end_utc
      AND 2 * (julianday(MIN(later.end_utc, sleep_sessions.end_utc))
               - julianday(MAX(later.start_utc, sleep_sessions.start_utc)))
          >= MIN(julianday(later.end_utc) - julianday(later.start_utc),
                 julianday(sleep_sessions.end_utc) - julianday(sleep_sessions.start_utc))
);
