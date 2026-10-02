# Health Auto Export fixtures (SYNTHETIC)

Every file here was generated from the documented JSON v2 shape
(help.healthyapps.dev/en/health-auto-export/export-format/workouts and /health-metrics),
not captured from Max's phone. Real pushes under `data/raw/health/` hold Max's own health data
and are never copied here; `metrics_v2_samples.json` reproduces their shape with invented
values instead. The day-summed fixtures remain best-effort readings of the docs.

| File | What it exercises |
|---|---|
| `workouts_v2_run.json` | One outdoor run, 40 min, 20 `heartRateData` samples spanning the whole workout |
| `workouts_v2_overlap.json` | The same run from "Apple Watch" and a phone app ("Nike Run Club"), start 90 s apart, duration within 5 %; the phone copy has no HR samples and a longer distance |
| `workouts_v2_recovery_only.json` | 45-min lift whose `heartRateData` spans only the ~2-min recovery series (upstream issue #60) |
| `workouts_v2_no_hr.json` | A ride with no `heartRateData` at all |
| `metrics_v2_days.json` | Days grouping: sleep one night ending 06:40 local, steps, HRV, resting HR, VO2 max, time in daylight for 3 days, plus an unknown metric (`mindful_minutes`) that must be ignored |
| `metrics_v2_sleep_dst.json` | A sleep session across the 2026-11-01 fall-back in America/New_York (wake day 2026-11-01) and a late nap ending 23:30 local whose UTC end is the next day |
| `metrics_v2_samples.json` | The shape the real pushes have (issue #3; Summarize Data is not honoured): per-sample `step_count` rows from "Apple Watch" and "iPhone" that overlap in time over 3 days (Watch wins one day, iPhone one, a tie one), fractional `time_in_daylight` minutes, `heart_rate_variability` rows with `start`/`end`/`heartbeatSeries`, one `resting_heart_rate` per day, two `vo2_max` readings on one day listed newest first, `heart_rate` rows that must be ignored quietly, and `sleep_analysis` as one row per stage segment: a night across midnight with two Awake segments and a 10-minute silence (one session), a 25-minute "Asleep" nap the same day, and a second night. Rows are shuffled. Golden arithmetic: `tests/ingest/test_samples.py` docstring |
| `metrics_v2_samples_dst.json` | The segment shape across the 2026-11-01 fall-back (a Deep segment from 01:30 EDT to 01:30 EST is one hour), a late nap on 10-30, and steps stamped in both offsets on 11-01 |
| `batch_part1.json`, `batch_part2.json` | A 7-day workouts export split across two POSTs (Batch Requests on) |
| `malformed.json` | Truncated JSON; must still be archived and answered 422 |

What the real archive settled (2026-10-02, shape only, no values copied here): the Health
Metrics push is ~18 MB of samples, not day sums, whatever the phone's Summarize Data setting
is doing (unconfirmed on the phone; the parser accepts both shapes). Step and daylight
rows are per-second slices with fractional `qty` and a `source` that is either one device or
two joined with `|` (the app's own label for a slice both devices covered). Sleep is one row
per stage segment (`value` Core / Deep / REM / Awake / Asleep, `startDate`/`endDate`, `qty`
hours); nights are contiguous segments; a nap is a lone `Asleep` segment. HRV comes ~11 times a
day as 60-second readings with a `heartbeatSeries`; resting HR once a day spanning the day;
`heart_rate` is pushed although the automation omits it. The push's window is the 7 whole
phone-local days ending YESTERDAY: today's samples and last night's sleep are never in it.

Assumptions the docs do not settle: v2 workouts carry no top-level `source`, so `source_app`
is the most frequent `source` on the workout's time-series entries; the aggregated sleep row
has no `awake` field in the docs (we accept it if present); the snake_case names
`heart_rate_variability`, `vo2_max`, `time_in_daylight` follow the documented naming rule but
are not listed on the page (only `step_count` and `resting_heart_rate` are).
