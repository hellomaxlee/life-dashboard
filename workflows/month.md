# month — the month feature

Owner: Theodora Makris (brief); Ingrid Halvorsen ratifies the voice. Tool: `tools/month.py`.
Code: `app/month/` (`spec.py` the contract, `prompt.py` the brief, `generate.py`, `store.py`).

## What it does

Once a month the model authors one JSON object: a title, a theme, a palette of 2 to 8 bright
colours, and for every day of the month a 16x16 plate in palette indices, a caption and a
note. `spec.parse_feature` is the only judge of what is stored; the renderer draws day N's
plate, then the month's calendar as the last page; while no feature is stored the
calendar is the whole screen.

The request carries the month, the year and the titles and themes of earlier features.
Nothing of Max's is in it: no metrics, no dates of his, no health data. That is why the spec
bans digits in the feature's text.

## Commands

```sh
uv run python -m tools.month                    # this month; one run if nothing is stored
uv run python -m tools.month --month 2026-11    # another month
uv run python -m tools.month --show             # stored title, theme, palette, day captions
uv run python -m tools.month --dry-run          # request size, worst-case cost, cap check
uv run python -m tools.month --force            # author it again; the old one stays if this fails
```

Exit 0 when a feature is stored for the month, 1 when not, 2 when refused (bad `--month`,
schema mismatch).

## When it runs by itself

Scheduler job `month_feature`: daily at 00:20 home time (misfire grace 20 h), and five
minutes after a service start when the month has no feature. A stored feature means no call.

## Spend and the guard

- Model: `[summary] model` in `config.toml`. `max_tokens` 32000 (thinking counts against
  it), streamed. Each call passes the summary's monthly cap check first and is recorded in
  `model_spend`; over the cap means no call.
- A run makes at most 2 calls: the second only after a rejection, with the reason.
- A run that called the model and stored nothing leaves a row in `month_feature_attempts`.
  Automatic runs: at most one a home-timezone day and three a month. `--force` skips this
  guard and never the cap.
- A stream that dies part-way may be billed by the API with no usage to record; the spend
  counter cannot see it.

## Rejections

A reply is rejected when it holds no JSON object, fails `spec.parse_feature` (day count,
16x16 art, palette index, dark colour, caption or note length, digits, undrawable
characters, too few lit cells, repeated art), or fails the voice check: the daily summary's
ban list (`app/summary/gate.check_ban`) on the title, theme and every caption and note,
words attributed to a person, digits in the theme.

## Replay and backup

The feature is authored output. `tools.replay --verify` leaves `month_features` out;
`--rebuild-live` does not touch it; `--snapshot`, `--diff` and the backup checksum include it
once a row exists.
