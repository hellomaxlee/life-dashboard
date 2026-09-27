# CHANGELOG

Running log, newest first. The Lead reads this to find root causes and prioritize.
Entry format: `## YYYY-MM-DD · <Title> (<Author>)` followed by terse bullets. Name the fixture combo on render/summary cycles.

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
