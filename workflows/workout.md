# workout: credit a day's quality workout by hand

When the Health Auto Export workouts feed does not deliver (or a session had no heart rate),
a real workout earns no dot. The override is Max's own word that the day held a quality
workout; the dashboard honours it and marks it manual. No heart rate, no zones, no load
figure: credit by assertion. See `notes.txt § Architecture assumptions`, Manual workout
override.

## CLI

```
uv run python -m tools.workout --note "4 mile run"               # today (home timezone)
uv run python -m tools.workout --date 2026-10-03 --note "..."    # that day; adding again
                                                                 # replaces the note
uv run python -m tools.workout --date 2026-10-03 --remove        # take it away
uv run python -m tools.workout --list                            # every override
```

Each add or remove recomputes the metrics rows at once (the scheduler's `recompute` on the
live db) and prints the day's `daily_metrics` row and its week's `weekly_metrics` row. A date
after today is refused. Exit 2 if the schema is behind (`tools.migrate`).

## LAN page

`GET /workouts` on the service (port 8080) shows a date + note form and the list of
overrides, each with a remove button. `POST /workouts` with `action=add|remove`, `date`,
`note` applies it, recomputes, and redirects back. No auth, no JavaScript, like the other
pages.

## What changes

- The day: `quality_workout` true, `workout_count` at least 1, `manual_workout` true,
  `manual_note` as given, `wins` gains `workout`. `workout_load` stays what the scored
  activities say (null when none). On a day with no scored activity the week's `load_bar`
  stands in as the day's TRIMP, so acute/chronic/balance do not read the day as rest.
- The week: one more `quality_workouts` unless the day already has a scored quality
  activity, in which case nothing changes. The streak, the daily win and the week-complete
  party follow. `week_complete_at` uses the override's recorded time when it is the dot that
  completes the week.
- A real workout that lands later for the same day merges with the override: still one dot.
- Today screen: gold disc, "WORKOUT DONE". Summary: the payload carries
  `day.manual_workout: true` (never the note); the fallback says "logged by hand" and never
  speaks of load; the model is told the same.

## Judged workouts (the fitness coach)

A third avenue beside a scored activity and an override (Max, 2026-10-07: "a sprint for
the bus shouldn't count but a 20 minute HIIT on a bike should, as should a recorded
workout"). Inside every metrics recompute, `judge_before` (`app/metrics/job.py`) finds days
in the current and previous week that have heart-rate data, no scored activity at the bar
and no override, and asks the model pinned in `[judge] model` to rule as a fitness coach.
What it sees (`app/metrics/judge_prompt.py`): the day's whole-day aggregates and, when the
Health Auto Export automation sends `heart_rate` aggregated by minutes, the day's *bouts*:
contiguous minutes at or above zone one (gaps up to 3 min joined), each as duration,
average, peak, zone minutes and Edwards load. Never a timestamp, never the minute series.
With daily aggregates only (`hr_resolution: daily`) the coach credits only an obvious
sustained effort; set the automation's heart-rate aggregation to **minutes** for the bouts.

- Listing: `uv run python -m tools.workout --list` prints judged days with verdict,
  confidence and the coach's reason; `/workouts` shows them under "Judged from heart rate".
- Removing: the Remove button (or `judge.deny`) records a `denied` verdict for those inputs,
  recomputes, and the dot goes; the day is judged again only if its inputs change.
- Never revoked: a `yes` stands until Max removes it; a later run on new inputs may turn a
  `no` into a `yes`, never the reverse.
- Precedence: a scored activity at the bar or an override wins; a judged day never adds a
  second dot. The summary payload carries `day.judged_workout` (never the reason); the
  fallback says "judged from heart rate".
- Cost: one call per day per distinct input set, at most the two-week window, cap in
  `[summary] monthly_cap_usd` is a hard stop; the day stays unjudged on any error.
- After a code change to the heart-rate parser, past days need `tools.replay
  --rebuild-live` (run-service.md section 16) before they carry `heart_rate_max`.

## Replay and backup

`manual_workouts` and `judged_workouts` are authored data (the second by the model). `tools.replay --verify` copies it into the scratch db
before recomputing and never compares the table; `--rebuild-live` leaves it alone; a backup
carries it like every table. Edge: an override removed from a day earlier than every stored
sample leaves an empty, truthful row that `--verify` lists as live-only until
`--rebuild-live`.
