---
name: source-replay
description: Replay archived raw payloads through ingest and diff the metrics table, proving idempotency and cross-source dedupe. Use when the user says "replay", "recompute metrics", "is ingest idempotent", after ANY change under app/ingest/ or app/metrics/, or when a metric looks wrong. Do NOT use to fetch new data from a source; that is tools.sync.
---

# source-replay

The proof that what is stored is what the archive says, that the same data twice changes nothing, and that a run seen twice counts once.

1. **Verify:** `uv run python -m tools.replay --verify`. It re-parses every parsed payload in `data/raw/` into a scratch database (`data/replay/scratch.db`), in `raw_archive.id` order, and diffs the scratch tables against LIVE. Exit 0 and `0 difference(s)` is the pass; exit 1 prints every differing row (`+` live only, `-` replay only) and any parsed payload whose raw file is missing.
2. **Read the notes.** `note: unrecorded: ...` names raw files with no `raw_archive` row (a push that never got recorded; its data is not in the db and it is not replayed). `note: unparsed: ...` names recorded payloads that never parsed. Neither is a difference; each is something to explain in the CHANGELOG bullet if it is new.
3. **Across a code change** (a parser or metrics edit you expect to move numbers): before the change `uv run python -m tools.replay --snapshot data/replay/before.json`; after it, `uv run python -m tools.replay --diff data/replay/before.json`. This compares live with live and never looks at the scratch db, so it shows what the change did to stored data once it has been re-ingested; it is not the idempotency proof. Step 1 is. Every difference is explained by the change and listed in the CHANGELOG.
4. **Dedupe fixture:** `uv run pytest tests/ingest/test_dedupe.py -q`. Then run the mutant: `DEDUPE_DISABLED=1 uv run pytest tests/ingest/test_dedupe.py -q` must FAIL. If it passes, the gate is not a gate; fix the test before anything else.
5. **Reconcile against the sources:** compare last week's activity count and total minutes with the Health app by hand; record the two numbers in the CHANGELOG bullet.
6. **Boundary check:** confirm the most recent activity after 22:00 local and the most recent sleep session sit on the expected local days.

The gate's own mutant: `uv run pytest tests/tools/test_replay_verify.py -q` doubles every step count in the parser path and requires `--verify` to exit 1 while `--snapshot` / `--diff` stay green.

With no database at all (rebuilding from the archive alone), `uv run python -m tools.replay --since 2000-01-01` replays every file in filename order and says on stderr that the order is approximate.
