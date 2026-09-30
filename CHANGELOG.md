# CHANGELOG

Running log, newest first. The Lead reads this to find root causes and prioritize.
Entry format: `## YYYY-MM-DD · <Title> (<Author>)` followed by terse bullets. Name the fixture combo on render/summary cycles.

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
