# CLAUDE.md

## Project
life-dashboard: a single-user life dashboard that turns Max's training, sleep, and reading into a glanceable picture of progress, first on a LAN web page and then on a Divoom Pixoo-64 II (assumed for design; not yet purchased). The backend ingests Apple Health (via Health Auto Export REST push) and a Goodreads RSS shelf; computes weekly quality-workout counts, a weeks-hit streak, daily small wins, and training load against stated goals; renders device-agnostic frames and short celebration clips; and writes one short daily summary in a balanced, wisdom-leaning voice that favors sustainable habits over streak anxiety. Runs on Max's Mac now, a Raspberry Pi later. Personal only.

**Done means:**
1. A day's Health Auto Export push and a Goodreads poll land in SQLite idempotently; replaying the same payloads changes no metric.
2. The same workout reported by two HealthKit source apps (Watch and a phone app that writes to Health) is one activity everywhere; no metric double-counts it.
3. Quality-workout counts per week, the weeks-hit streak, daily wins, training load (acute, chronic, balance), and books-this-year match hand-computed golden cases.
4. Every rotation screen and both celebration clips render in the browser at 1x and 8x, are legible at 1x under LED gamma, and reach the Pixoo through an adapter.
5. The daily summary cites only numbers present in the metrics table, stays within a configured monthly budget, and falls back to rule-based copy when the model is unavailable.
6. The service survives a restart and a power cut with no data loss; a backup restores to an identical metrics table.

**Non-goals:** multi-user or auth; a phone app; coaching plans or workout prescription; strength-set logging; any Strava integration or import (its API needs a paid subscription; Apple Health has everything the goal model needs); replacing the Health app as the system of record for raw data.

## Stack
Python 3.12 · uv · FastAPI · SQLite (stdlib `sqlite3`, hand-written migrations) · APScheduler · httpx · Pillow (frames) · pytest · ruff
- SQLite over Postgres: one user, one box, one file to back up.
- Pillow over a browser canvas: frames are PNG bytes the device adapters transport; no browser in the render path.
- LLM: Anthropic SDK, model id pinned in `config.toml`, never hardcoded; load `/claude-api` before choosing or changing it.
- Health Auto Export (iOS) pushes JSON to `POST /ingest/health`; Goodreads via the public `read` shelf RSS.

## Commands
| Task | Command |
|------|---------|
| Install | `uv sync` |
| Run API (port 8080) | `uv run fastapi dev app/main.py --port 8080` |
| Test | `uv run pytest` |
| Lint/format | `uv run ruff check --fix . && uv run ruff format .` |
| Render a frame | `uv run python -m tools.render --date YYYY-MM-DD --scale 8` |
| Replay raw archive | `uv run python -m tools.replay --since YYYY-MM-DD` |
| Sync sources now | `uv run python -m tools.sync --source goodreads` |

## Code Style
ruff is the authority (format + lint). Type hints on every public function. No comments where the name says it. Timestamps stored as UTC ISO strings, displayed in the home timezone from config. Small modules named for the thing they own: `app/ingest/`, `app/metrics/`, `app/render/`, `app/summary/`, `app/web/`.

## Invariants
Rules that survive any refactor. Break one and the dashboard is wrong even if the tests pass.
- **Health data stays home.** Raw samples never leave the LAN. Outbound calls are the Goodreads pull and the summary call, which sends daily aggregates only, never raw samples or sub-day timestamps. An egress test enforces the allowlist.
- **One canonical activity.** A workout reported by more than one HealthKit source (Watch, a phone app) merges into one record with every provenance kept; no metric counts it twice.
- **Raw before parsed.** Every inbound payload is archived verbatim in `data/raw/` before parsing, so any metric can be recomputed from scratch.
- **Day and week boundaries are America/New_York local.** Weeks run Monday to Sunday. A sleep session belongs to the day you wake.
- **Progress is weekly.** A quality workout is ≥ 45 min with average HR at or above the zone-2 floor (113 bpm, from max HR 189). Three per week is the target; the streak counts weeks that hit it. Any day without one is rest, and rest never breaks anything. See `notes.txt § Goal model`.
- **Every displayed number traces to a metrics row.** The model never computes or invents a figure; a grounding gate rejects any summary whose numbers are not in its payload.
- **The frame is device-agnostic.** Renderers emit a 64x64 RGB frame or a clip of them with per-frame durations; adapters only transport. Legibility is judged at 1x under LED gamma, never at browser zoom.
- **The display never blanks.** Model unavailable or over budget means rule-based copy, not an empty frame.
- **Voice: balanced, sustainable, no shame.** Rest counts as progress. Wins are celebrated on the device; misses are shown plainly, never nagged. Streak-anxiety copy ("don't break the chain") and moralizing are banned by a ban list the summary gate enforces.

## Working Rules
- Verify by execution. A claim of "fixed" names the command, fixture, or measurement that proved it.
- A gate is not a gate until a mutation makes it red. New checks ship with the mutant that fails them, and the gate runs on the committed tree.
- Doubt the instrument first. When a harness reports a defect, prove it measures the product and not its own copy of the data.
- Once per cycle, look at one real frame at 1x and read one real summary end to end.
- Model turns cost money. Read the spend counter at the start and end of any round that calls the model; the monthly cap in `config.toml` is a hard stop.
- Report outcomes faithfully. Failed test output goes in the message; skipped steps are named.

## Claude Behavior
- Work agentically. Make changes directly, no step-by-step narration (exception: plan mode).
- Deploy agents in parallel when tasks are independent and file sets are disjoint.
- Never stop mid-task for permission. Summarize in bullets at the end.
- Commit and push when work is complete. Be concise; no unrequested features.

## Team Workflow
Source of truth: `/agent-creation` (roster contract) and `/performance-review` (review cycle).
- **Lead:** Ingrid Halvorsen. Reads `CHANGELOG.md` and `notes.txt` first, frames the task as verifiable claims, sets acceptance criteria before work starts, closes issues only after verification.
- **No Engagement Partner** (personal, single-user; no cost or market dimension beyond the model budget, which Ingrid owns).
- **Arbitration:** Bartek Zieliński owns data-truth and metric-semantics calls; Lucía Ferrer owns display, legibility, and hardware calls; Ingrid owns what-counts-as-progress and the summary's voice (Theodora drafts, Ingrid ratifies); Ingrid resolves cross-cutting calls. Device purchase is Max's call only.
- **Flow:** Lead frames → Associates build → Managers QA → a Manager commits + pushes to `dev` → one bullet per contributing agent's log and one CHANGELOG entry.
- **Branching:** `dev` is the working branch. `main` is never committed to directly (pre-push hook blocks it); promote via `dev → main` PR only when Max asks.
- **Review** every 15 pushes or via `/performance-review`.

## Iteration Rule
Every render or summary test cycle uses a NEW fixture combination across: day type (train / rest / race-week / travel) × data completeness (all sources / workout without HR / sleep missing / Health delayed) × streak state (alive / broken last week / never started) × season position (base / peak / off). Name the combo in the CHANGELOG entry. See `/rotate-fixture`. Reason: the frame layout and the summary voice fail differently in each cell; a fixture reused twice hides the cells it never touched.

## Project-Local Skills (`.claude/skills/`)
- **ship-dev** — ruff → pytest → CHANGELOG → agent logs → push to `dev` only.
- **frame-preview** — render a fixture day to PNG at 1x and 8x and screenshot it; the eye test every display change must pass.
- **source-replay** — replay archived raw payloads through ingest and diff the metrics table; the idempotency and dedupe proof.

## Structure
- `app/` — FastAPI service: `ingest/`, `metrics/`, `render/`, `summary/`, `web/`, `main.py`
- `tools/` — CLIs per workflow (`render`, `replay`, `sync`, `backup`); `workflows/` — one markdown spec per tool
- `fixtures/` — sanitized raw payloads and golden metric cases; `tests/` — pytest
- `data/` — SQLite db, `raw/` archive, backups (git-ignored)
- `agents/` — `[Role] First Last.md`; `agents/_old/` fired/demoted; `agents/performance/` reviews
- `additional/` — device adapters and third-party notes; `_old/` — archive, never delete
- `notes.txt` — intake, competency map, goal model, architecture decisions, risks; **document every critical assumption here**
- `CHANGELOG.md` — one dated entry per push, newest first

## GitHub
Remote: `https://github.com/hellomaxlee/life-dashboard.git` (private), `origin`. Working branch `dev` tracks `origin/dev`; promote via `dev → main` PR. Use the `gh` CLI against it. Issue closure: the last comment states testing extent, verification method, and reason for closure.
