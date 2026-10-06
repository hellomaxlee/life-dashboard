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

## Replay and backup

`manual_workouts` is authored data. `tools.replay --verify` copies it into the scratch db
before recomputing and never compares the table; `--rebuild-live` leaves it alone; a backup
carries it like every table. Edge: an override removed from a day earlier than every stored
sample leaves an empty, truthful row that `--verify` lists as live-only until
`--rebuild-live`.
