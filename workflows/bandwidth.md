# tools.bandwidth

Reads the two bandwidth scores the metrics engine stores in `daily_metrics`: the fuller
score the Books lamp is coloured by (`bandwidth_full`, `app/metrics/bandwidth_full.py`)
and the two-term score kept beside it for comparison, which nothing displays
(`bandwidth`, `app/metrics/bandwidth.py`). It computes nothing and writes nothing. Rules:
`notes.txt § Goal model`, "Bandwidth" and "Bandwidth, the fuller score". Proofs and
figures: `additional/bandwidth-index.pdf`.

## Commands

```
uv run python -m tools.bandwidth                 # the last 14 days
uv run python -m tools.bandwidth --days 60
uv run python -m tools.bandwidth --date 2026-11-20
```

## Reading a line

```
2026-11-20  two  45.8 gold    full  46.5 orange   98/100  sleep_timing -4.0, recent_nights +3.3, sleep_hr -2.0
```

- `two`: the two-term score and the colour it would show on its own stops (33.33, 50,
  66.67).
- `full`: the fuller score and the colour the lamp shows for it (stops 39.75, 50, 60.25,
  derived from the weights).
- `98/100`: how much of the fuller score's weight had a term behind it that day. Under 60
  the lamp does not read it: no cue and no colour, the plain gold lamp, whatever band the
  line names.
- The last column is the three terms that moved the fuller score most, in points.

`--date` prints every term by domain with its weight, z, points, value and what it was
measured against, and re-adds the points to the stored score.

## When a score is `-`

No reading: no night on record for that day (two), or neither last night's sleep nor a
sleeping heart rate (full). Today before noon with no night yet shows `-` too, while the
lamp carries yesterday's colour (`bandwidth_full_lamp`, and `carried_from` in the trace).
A day the engine has not recomputed since the code changed also shows `-` for the fuller
score: `uv run python -m tools.metrics --recompute`.

## Exit codes

0 printed; 2 the live db's schema is behind (`uv run python -m tools.migrate`).
