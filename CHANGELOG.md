# CHANGELOG

Running log, newest first. The Lead reads this to find root causes and prioritize.
Entry format: `## YYYY-MM-DD · <Title> (<Author>)` followed by terse bullets. Name the fixture combo on render/summary cycles.

## 2026-10-02 · Phase 1b render and Phase 4 run-it, with independent audits (Bartek Zieliński, Lucía Ferrer)
- Sequence: Diego (render) and Callum (scheduler, backup, drill) built in parallel worktrees → manager QA by Lucía and Bartek → merged → Callum built the one dependent piece, the device rotation job → independent audits by new hires Noor Rahimi (display) and Kwabena Osei (reliability) → fix rounds → Kwabena re-audit → final fixes. Gate on the shipped tree: ruff clean, 415 passed (38 before), `tools.drill` four stages PASS.
- Phase 1b: `app/render/` frame and clip contract (64x64 RGB, per-frame ms), 5x7 and 3x5 bitmap fonts drawn in code, linear-PWM LED gamma emulator, Week / Today / Books screens, Claude usage bar (issue #2) with 60 and 85 ticks, stale marker and "no data" state, sparkle and party clips, `/preview` page, `tools.render`, file adapter, Pixoo adapter (fake transport only, LAN-literal hosts only, proxies ignored). Summary is word-wrapped pages, not a scroll; every clip is ≤ 59 frames and the adapter refuses longer ones. Renderer takes a typed `DayView` and computes no metric; keys Phase 2/3 must write are in `notes.txt § Architecture assumptions`, including the rule that day and week rows are written at rollover.
- Issue #2 acceptance: 41.2 % fills 24 of 60 px (fill is floored, asserted on pixels raw and after gamma); missing reading draws "CLAUDE NO DATA" with the rest of the Week screen pixel-identical; mutant `USAGE_BAR_MUTANT_REMAINING=1` turns the golden red (14 failed in `tests/render`).
- Fixture combos rendered and eye-tested at 1x under gamma: train / all sources / alive / base; rest / sleep missing / broken last week / base; travel / Health delayed / alive / off; race-week / workout without HR / never started / peak; train / all sources / alive / peak (Diego). travel / workout without HR / broken last week / off (Lucía). race-week / all sources / broken last week / base; rest / Health delayed / alive / peak (Noor). Lead look at 1x: real day 2026-10-01 and the race-week base fixture; what each frame says in two seconds is in the session report. Rulings: 60/85 thresholds and gamma curve approved (Lucía); a false dot reads "NO DOT YET" (Ingrid); percent label and bar fill never round up.
- Phase 4: APScheduler in the FastAPI lifespan (`claude_usage_watch` every 30 s, read at most every 900 s; `nightly_backup` 03:15 New York; hourly `backup_overdue_check`; `device_rotation` only when `device.pixoo_host` is set), `tools.backup` (sqlite backup API + hard-linked raw archive, manifest with db and raw hashes, `--restore`, `--verify latest`), `tools.drill` (kill -9 at four ingest stages), `tools.migrate`, `tools.replay --verify`, migration 002 (sleep and wellness: last received wins), config range checks at boot, runbook sections 10–16 and `workflows/backup.md`.
- Defects found and fixed in ingest along the way: a payload killed mid-parse was answered `duplicate` forever (drill red before); a first fix (`superseded`) stranded a Workouts push behind a Metrics push (audit) and was replaced by apply-in-arrival-order recovery with hash-checked re-apply; the old replay proof compared live with live (a steps×2 parser mutant passed) and is replaced by `tools.replay --verify`; replay now follows `raw_archive.id` order; a corrected night no longer leaves the first report; merged activities are a function of the set of copies, not arrival order (264-history property test); migrations run only at service start or via `tools.migrate`.
- Mutants: every gate added this round has a named red mutant; counts by reviewer: Bartek 17 of 18 red, Lucía 10 of 12, Noor 36 of 42, Kwabena 25 of 38 then 16 of 18; every survivor that mattered now has a test. Still surviving by design: a host assembled at runtime (static egress scan limit).
- Not proven: power cut (only kill -9 is drilled; Done-means 6 wording stands unproven); anything on Pixoo hardware; real Mac sleep/wake; launchd install (still Max's call); a browser screenshot of `/preview`. Deferred dedupe limits (re-issued ids, three-way chains, workouts deleted in Health) are in notes and issue #4.
- Live box: nothing was run against the live database after the fixes. It is at schema 1 and needs, in order: `uv run python -m tools.backup`, stop the service, `uv run python -m tools.migrate`, `uv run python -m tools.replay --verify`, start the service (runbook §16). Rehearsed on a scratch copy of live: sleep 216 → 216 rows, wellness 41 → 38, `--verify` 0 differences. A pre-migration copy is at `data/backups/pre-migration-002-life.db`.
- Real-data finding (issue #3): the real Health Auto Export push is not day-summed (81,270 step samples, 15,018 daylight samples, sleep as per-stage segments) while the parser assumes one row per day, so stored steps are a last sample ("STEPS 2") and sleep hours are absent. The live db also still holds the three synthetic fixture pushes from the 2026-09-30 LAN test.
- `CLAUDE.md` Commands table is stale and was not edited: `uv run fastapi dev` needs `fastapi[standard]` (use `uv run uvicorn app.main:app --host 0.0.0.0 --port 8080`); `tools.sync --source goodreads` is not built; `tools.backup`, `tools.migrate`, `tools.drill`, `tools.replay --verify` are missing.

## 2026-10-01 · Claude usage hook and reader (Bartek Zieliński)
- Issue #2, Phase 1a half, built by Tendai: `tools/claude_usage_hook.py` (stdlib only, silent, always exit 0, atomic write of `rate_limits` + `captured_at_utc` to `data/claude_usage.json`; a feed without `rate_limits` keeps the last reading), `app/ingest/claude_usage.py` (archive raw to `data/raw/claude_usage/` first, then `claude_week_used_pct`, `claude_week_resets_at`, `claude_week_captured_at` merged into the capture day's `daily_metrics` row), `tools.sync --source claude_usage`, and `tools.replay` now replays both sources.
- Wired on Max's Mac: one line added to `~/.claude/statusline-command.sh` after `input=$(cat)`; `settings.json` untouched because it already had a status line. Decision recorded in `notes.txt § Architecture assumptions`.
- QA (Bartek): ruff clean; 38 passed; mutant `CLAUDE_USAGE_MUTANT_FIVE_HOUR=1` turns the golden case red (73.5 ≠ 41.2, 6 failed); hook runs in ~30 ms; hand-fed 41.2 payload through the status-line script → line printed, file written; live session then wrote a real reading, `tools.sync` → `ok used_pct=33.0`, replay twice → identical checksum `9cbd9e76…`, `--diff` → 0 differences.
- Fixtures are synthetic from the documented status-line shape (`fixtures/claude_usage/README.md`). Not in scope: the Week-screen bar and stale marker (Diego, Phase 1b), scheduling the reader (Callum, Phase 4).

## 2026-09-30 · Health ingest service: raw archive, SQLite, idempotent replay, dedupe (Bartek Zieliński)
- Phase 0 + Phase 1a health path built by Tendai: `config.toml` with every goal-model key, WAL SQLite with versioned migrations (`app/migrations/001_initial.sql`), `POST /ingest/health` archiving bytes to `data/raw/health/` before any parse, v2 parser for workouts (heartRateData samples) and day-grouped metrics (sleep, steps, HRV, resting HR, VO2 max, daylight), dedupe by start window 5 min / duration 10 % with every provenance kept, `hr_incomplete` flag when the HR trace spans under 25 % of the workout (upstream bug #60), `GET /` status page, `tools.replay --since/--snapshot/--diff` sharing the endpoint's parser.
- Callum: LaunchAgent + install/uninstall scripts under `additional/launchd/`, runbook `workflows/run-service.md`, static egress allowlist test with two red mutants.
- QA (Bartek): ruff clean; 28 passed; `DEDUPE_DISABLED=1` turns 2 dedupe tests red; replay twice → identical checksum `986fa3ca…`; `--diff` against the live db → 0 differences; live run on 192.168.1.171:8080: overlap fixture → 1 activity, 2 provenances (Apple Watch, Nike Run Club), re-post → `duplicate`. macOS firewall is off, so no allow rule needed.
- Ratified: day-grouped rows keep the phone's own calendar date rather than a UTC→New York re-projection; the phone already summed the day locally. Sleep wake-day and workout times do go through UTC→America/New_York (DST fixture passes).
- Fixtures are synthetic from the documented v2 shape (`fixtures/health/README.md`); the first real push becomes the canonical fixture. Metric names `heart_rate_variability`, `vo2_max`, `time_in_daylight` are inferred from the docs' snake_case rule; unknown names surface in `ingest_log.unknown_metrics` and on the status page.
- Not installed: the LaunchAgent (persistent login item; Max runs `bash additional/launchd/install.sh`). Not in scope: Goodreads poller, metrics engine, Claude usage hook.

## 2026-09-29 · Pull cadence: every 6 hours, two automations (Ingrid Halvorsen)
- Max set Sync Frequency to every 6 h while configuring the app. One Workouts and one Health Metrics automation replace the 06:30/22:00 pairs. Week closes after the first push following Monday 00:00 local. Raw archive grows ~100 MB/month at 7-day payloads; accepted.

## 2026-09-29 · Health Auto Export settings decided: JSON v2, four automations (Ingrid Halvorsen)
- Max began configuring the app ahead of the service. Docs checked: one Data Type per automation, so Workouts + Health Metrics × 06:30/22:00 = four, not two. Export Version 2 chosen for per-workout `id` and per-sample `source` (dedupe keys) and `heartRateData` samples. Date Range Previous 7 Days (no 2-day option; idempotent ingest absorbs overlap). Health Metrics grouped by Days; standalone Heart Rate dropped in favour of the workout series.
- New ingest requirement from upstream bug #60: flag workouts whose HR trace spans under a quarter of the duration as `hr_incomplete`, no dot. Mac LAN IP 192.168.1.171 recorded in notes step 3. Service not yet built; pushes fail until Phase 1a.

## 2026-09-27 · Load bar calibrates itself (Ingrid Halvorsen)
- Max: calibration should be automatic. First bar from the first three ≥ 4-mile runs; re-calibrated every 3 months from the trailing window; changes apply forward only so the streak never rewrites. "Say calibrate" removed from Max's checklist.

## 2026-09-27 · Claude usage meter assigned (Ingrid Halvorsen)
- Max asked whether the team had it. It was designed in issue #2 but unowned. Now in the goal model and Phase 1a/1b: Tendai builds the status-line hook and reader (QA Bartek), Diego draws the bar with its stale marker (QA Lucía). Pi-hop caveat dropped since the Mac is the home box.

## 2026-09-27 · Wellness signals chosen (Ingrid Halvorsen)
- Max picked four automatic Watch signals: HRV, resting HR, VO2 max, time in daylight. All logged-only metrics declined. Stored daily with 28-day baselines and deviation bands; web-page trends and summary facts only, never a target, win, or panel. `wellness_daily` added to the schema plan; issue #1 step 3 data types extended.

## 2026-09-27 · Voice brief v1; Pixoo reaffirmed over e-ink; Pi deferred (Ingrid Halvorsen)
- Max named the sources (Stoic, Aristotelian habit, Buddhist non-attachment) and admired qualities (steady self-leadership, resilience, self-awareness) with a hard rule: no impersonation, no capitalizing on any voice. Repetition minimized by three coded mechanisms (lens rotation, 14-line memory, trigram similarity gate). Ten hand-written lines across fixture cells and a seed ban list written to `notes.txt § Voice brief`. Theodora's Phase 3 prompt derives from it.
- E-ink weighed for the text-heavy summary; Max keeps the Pixoo for colour and celebration. Raspberry Pi deferred; Mac is the home box.

## 2026-09-27 · Raspberry Pi guide; Mac confirmed as home box (Ingrid Halvorsen)
- Max: the Mac is plugged in most of the time and missed pushes are acceptable. Mac stays the home box; the Pi is a learning project. `additional/raspberry-pi-guide.tex` (+ PDF) written for a first-time Pi user: shopping list, headless flash, SSH and keys, ten Linux commands, uv + Python 3.12, clone on `main`, systemd unit, data migration with replay check, pull-style nightly backup, care and troubleshooting.
- Guide assumes `tools.backup --out` and `tools.replay --since` as specified in CLAUDE.md; both are still to be built.

## 2026-09-27 · Issue #1 steps 1–2 done; Goodreads feed verified (Ingrid Halvorsen)
- Max added `GOODREADS_RSS_URL` to `.env` (git-ignored) and confirmed the goal numbers. Feed fetched once: HTTP 200, 75 items. `.env.example` key renamed to match.
- Data finding: only 19 of 75 books carry a read-at date, 1 in 2026; date-added is always present (1 in 2026). Reading rule gains a date-added fallback with a date-inferred mark. Bartek to ratify.

## 2026-09-27 · Pulls at 06:30 and 22:00 (Ingrid Halvorsen)
- Max settled on two pushes: 06:30 (wake-up) and 22:00. Today panel shows today's dot as of the last push with an "as of" mark; a post-22:00 workout lands at 06:30. Week closes after Monday 06:30. Issue #1 step 3 asks for two automations.

## 2026-09-27 · Goal model v2: effort load, zones, twice-daily pulls (Ingrid Halvorsen)
- Max's answers: every type counts; average HR was unfair to lifting. New rule: Edwards load (Z1–Z5 minutes ×1–5) ≥ a bar calibrated to a 4-mile run; placeholder 100 until three real runs set it. Zones by percent of max HR 189. No HR samples → no dot. Duration floor and avg-HR test retired; "whatever is most consistent" → one rule, no second path.
- Pulls: Health Auto Export at 05:00 and 22:00, Goodreads at 06:00. Week closes after Monday 05:00. Today panel gains an "as of" mark.
- New risk: Health Auto Export may not carry per-workout HR samples; verified on day one. Issue #1 step 3 updated.

## 2026-09-27 · Strava removed entirely (Ingrid Halvorsen)
- Max: not optional, gone. `tools.backfill` and the bulk-export path deleted from CLAUDE.md, notes, profiles, and issue #1. Non-goals now exclude any Strava integration or import. Dedupe referent is Watch + phone app in HealthKit only.

## 2026-09-27 · Strava dropped as a source (Ingrid Halvorsen)
- Strava's API requires a paid subscription (developers.strava.com, checked 2026-09-27). Max chose to drop it. Apple Health via Health Auto Export is the sole workout source; Strava-app recordings reach HealthKit through the app's Health integration.
- Dedupe survives with a new referent: same workout from two HealthKit source apps, or a backfill overlapping a live push. `tools.backfill` added to the plan for a one-time bulk-export import.
- CLAUDE.md (Project, Done 1–2, Non-goals, Stack, Commands, Invariants, Iteration axis), notes.txt, Tendai and Bartek profiles, source-replay skill, `.env.example` updated. Issue #1 item 1 rewritten.

## 2026-09-26 · Steps are not a target (Ingrid Halvorsen)
- Steps dropped from daily wins and config at Max's request; the quality workout is the win that matters. Steps stay on the Today screen as a plain number. Daily wins are now sleep ≥ 7 h, quality workout, book finished.

## 2026-09-26 · Threshold 45 min, HR set; setup issue opened (Ingrid Halvorsen)
- Quality workout floor raised 40 → 45 min at Max's request. Max HR 212 − 23 = 189; zone-2 floor 0.60 × 189 = 113 bpm. `notes.txt § Goal model`, CLAUDE.md invariant, and risks updated.
- Claude subscription usage bar on the Week screen: researched, see issue #2. Setup to-dos for Max: issue #1.

## 2026-09-26 · Goal model v1 and build plan (Ingrid Halvorsen)
- Intake round with Max (three question rounds). Progress is weekly: 3 quality workouts (≥ 40 min, avg HR ≥ zone-2 floor), any sport; rest is any other day; streak = weeks hit. Daily wins: sleep ≥ 7 h, steps ≥ 8,000, workout, book finished. 12 books/yr. Timezone America/New_York.
- Device: Pixoo-64 II assumed, not purchased. Rotation: Week → Today → Books+summary; load stays on the web page. Party clip on the third dot; small reinforcement per daily win. Frame contract widened to clips.
- Hosting: Mac now, Pi later with a beginner guide.
- Written to `notes.txt § Goal model` and `§ Plan`; CLAUDE.md Project, Done, and Invariants aligned. Open: `zone2_floor_bpm`, Strava API app, Goodreads shelf URL (Max).

## 2026-09-26 · Connect GitHub remote (Ingrid Halvorsen)
- `origin` → github.com/hellomaxlee/life-dashboard (private). `dev` pushed and tracking. `main` bootstrap left to Max (pre-push hook guards it). Reusable `/connect-repo` skill written to `~/.claude/skills/`.

## Kickoff
Roster and first tasks, decided 2026-09-26:
- Ingrid Halvorsen (Lead) — write `notes.txt § Goal model` v1: the event/load target, the streak and rest-day rules, the home timezone, the reading count; acceptance criteria for every week-1 task below.
- Bartek Zieliński (Manager) — canonical schema + migrations (`raw_archive`, `activities`, `sleep_sessions`, `daily_metrics`, `books`), the cross-source dedupe rule written as a spec with an overlap fixture, and the golden-case harness skeleton.
- Lucía Ferrer (Manager) — hardware memo: Pixoo-64 II vs Tidbyt Gen 2 vs DIY Pi + HUB75 vs LED bar, scored against open local API, brightness control, 64x64, Wi-Fi stability, price; plus the frame contract (64x64 RGB PNG, palette, gamma) all renderers target.
- Tendai Moyo (Associate) — `POST /ingest/health` (idempotent, archives raw first), Strava OAuth + activity sync with token refresh and rate-limit backoff, Goodreads RSS poller; fixtures for each.
- Fatima El-Amrani (Associate) — training-load engine (HR-based TRIMP, acute/chronic EWMA, balance) and streak engine with declared rest days and home-timezone day boundaries; hand-computed golden tests.
- Diego Almeida (Associate) — Pillow renderer: bitmap font, LED-safe palette, three layouts (today, streak, load); LAN preview page serving 1x and 8x.
- Theodora Makris (Associate) — summary prompt v1 from the voice brief, ban list, grounding gate, deterministic fallback copy, fixed-judge paired harness, spend counter.
- Callum Reid (Associate) — run it on the home box: launchd/systemd unit, APScheduler jobs, SQLite backup + restore test, `.env` secrets, egress allowlist test, power-cut recovery drill.

## 2026-09-26 · Scaffold (Ingrid Halvorsen)
- Directory, CLAUDE.md, roster, notes, and project-local skills created via /new-directory. `uv init --bare`; no dependencies installed yet.
