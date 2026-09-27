---
name: source-replay
description: Replay archived raw payloads through ingest and diff the metrics table, proving idempotency and cross-source dedupe. Use when the user says "replay", "recompute metrics", "is ingest idempotent", after ANY change under app/ingest/ or app/metrics/, or when a metric looks wrong. Do NOT use to fetch new data from a source; that is tools.sync.
---

# source-replay

The proof that the same data twice changes nothing and that a run seen twice counts once.

1. **Snapshot the metrics table:** `uv run python -m tools.replay --snapshot data/replay/before.json`.
2. **Replay:** `uv run python -m tools.replay --since <date>` re-parses `data/raw/` into a scratch database and recomputes every metric.
3. **Diff:** `uv run python -m tools.replay --diff data/replay/before.json`. Expected on an unchanged codebase: zero differences. On a metrics change: every difference is explained by the change and listed in the CHANGELOG.
4. **Dedupe fixture:** `uv run pytest tests/ingest/test_dedupe.py -q`. Then run the mutant: `DEDUPE_DISABLED=1 uv run pytest tests/ingest/test_dedupe.py -q` must FAIL. If it passes, the gate is not a gate; fix the test before anything else.
5. **Reconcile against the sources:** compare last week's activity count and total minutes with the Strava app and the Health app by hand; record the three numbers in the CHANGELOG bullet.
6. **Boundary check:** confirm the most recent activity after 22:00 local and the most recent sleep session sit on the expected local days.
