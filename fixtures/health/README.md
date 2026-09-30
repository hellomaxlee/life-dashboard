# Health Auto Export fixtures (SYNTHETIC)

Every file here was generated from the documented JSON v2 shape
(help.healthyapps.dev/en/health-auto-export/export-format/workouts and /health-metrics),
not captured from Max's phone. The first real push archived under `data/raw/health/` becomes
the canonical fixture and replaces these; until then treat field presence and units as
best-effort readings of the docs.

| File | What it exercises |
|---|---|
| `workouts_v2_run.json` | One outdoor run, 40 min, 20 `heartRateData` samples spanning the whole workout |
| `workouts_v2_overlap.json` | The same run from "Apple Watch" and a phone app ("Nike Run Club"), start 90 s apart, duration within 5 %; the phone copy has no HR samples and a longer distance |
| `workouts_v2_recovery_only.json` | 45-min lift whose `heartRateData` spans only the ~2-min recovery series (upstream issue #60) |
| `workouts_v2_no_hr.json` | A ride with no `heartRateData` at all |
| `metrics_v2_days.json` | Days grouping: sleep one night ending 06:40 local, steps, HRV, resting HR, VO2 max, time in daylight for 3 days, plus an unknown metric (`mindful_minutes`) that must be ignored |
| `metrics_v2_sleep_dst.json` | A sleep session across the 2026-11-01 fall-back in America/New_York (wake day 2026-11-01) and a late nap ending 23:30 local whose UTC end is the next day |
| `batch_part1.json`, `batch_part2.json` | A 7-day workouts export split across two POSTs (Batch Requests on) |
| `malformed.json` | Truncated JSON; must still be archived and answered 422 |

Assumptions the docs do not settle: v2 workouts carry no top-level `source`, so `source_app`
is the most frequent `source` on the workout's time-series entries; the aggregated sleep row
has no `awake` field in the docs (we accept it if present); the snake_case names
`heart_rate_variability`, `vo2_max`, `time_in_daylight` follow the documented naming rule but
are not listed on the page (only `step_count` and `resting_heart_rate` are).
