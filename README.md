# life-dashboard

A single-user life dashboard: Apple Health and Goodreads in, a glanceable picture of weekly progress out, on a LAN web page and a 64x64 Divoom Pixoo LED panel.

![The five rotation screens: Today, Weather, Week, Month, Books](docs/images/rotation.png)

## What it does

- Ingests Apple Health (pushed by the Health Auto Export iOS app) and a Goodreads `read` shelf (RSS).
- Scores every workout by effort: minutes in each heart-rate zone, weighted 1 to 5. Enough load earns a dot; three dots a week is the target, and the streak counts weeks that hit it.
- Tracks daily small wins (sleep goal met, workout done, book finished), training load (acute, chronic, balance), and books read this year.
- Shows a city panel between the personal screens: subway and bus alerts from the MTA and the day's weather from Open-Meteo and the National Weather Service.
- Writes one short daily line in a balanced, wisdom-leaning voice. Rest counts as progress; nagging and streak anxiety are banned by a gate.
- Once a month, the model authors a theme, a palette, and a 16x16 pixel plate for every day, with no personal data in the request.
- Renders device-agnostic 64x64 frames and short celebration clips with Pillow; adapters only transport them to the panel or the browser.

## Screens

| Today | Week | Books |
|---|---|---|
| ![Today](docs/images/today.png) | ![Week](docs/images/week.png) | ![Books](docs/images/books.png) |

| Weather | Transit | Month |
|---|---|---|
| ![Weather](docs/images/city-weather.png) | ![Transit](docs/images/city-transit.png) | ![Month](docs/images/month.png) |

Small wins and a completed week get a short celebration, sent to the panel as a sequence of stills:

| Workout win | Sleep win | Week done |
|---|---|---|
| ![Workout sparkle](docs/images/sparkle-workout.gif) | ![Sleep sparkle](docs/images/sparkle-sleep.gif) | ![Week party](docs/images/party.gif) |

The LAN preview page shows every screen at 1x through an LED-gamma emulator and at 8x for pixel inspection. Legibility is judged at 1x, never at browser zoom.

![Preview page](docs/images/preview-page.png)

## How it holds together

- **Raw before parsed.** Every inbound payload is archived verbatim before parsing, so every metric can be recomputed from scratch. Replaying the same payloads changes nothing.
- **One canonical activity.** The same workout reported by the Watch and a phone app merges into one record with both provenances kept; no metric counts it twice.
- **Health data stays home.** Raw samples never leave the LAN. The summary call sends daily aggregates only, the month call sends no personal data, and the weather feeds get coordinates rounded to two decimals. An egress test enforces the allowlist.
- **Every displayed number traces to a metrics row.** A grounding gate rejects any model-written line whose numbers are not in its payload.
- **The display never blanks.** Model unavailable or over the monthly budget means rule-based copy, not an empty frame.
- **Day and week boundaries are local time**, Monday to Sunday. A sleep session belongs to the day you wake.
- Gates ship with the mutant that turns them red. Every render cycle uses a new fixture combination (day type × data completeness × streak state × season) so the layout and the voice are tested across the matrix.

## Stack

- Python 3.12, uv, FastAPI, SQLite (stdlib, hand-written migrations), APScheduler, httpx, Pillow, pytest, ruff.
- Anthropic SDK for the daily line, the month feature, and a heart-rate "coach" that can credit a sustained effort the feed missed. Model ids and the monthly cap live in `config.toml`.
- Runs on a Mac under launchd today; a Raspberry Pi guide is in `additional/`.

## Run

```sh
uv sync
cp .env.example .env            # Goodreads RSS URL, Anthropic key, optional ingest token
uv run fastapi dev app/main.py --port 8080
uv run pytest
uv run python -m tools.render --fixture fixtures/days/train__all-sources__alive__base.json
```

Point Health Auto Export at `http://<host>:8080/ingest/health`. The preview page is at `/preview`, the status page at `/`, and `/workouts` lists and overrides what counted.

## Layout

- `app/` — `ingest/`, `metrics/`, `render/`, `summary/`, `month/`, `city/`, `jobs/`, `web/`
- `tools/` — one CLI per workflow (render, replay, sync, backup, summary, month, city, pixoo_check); `workflows/` — a spec per tool
- `fixtures/` — sanitized payloads, golden metric cases, and fixture days; `tests/` — pytest
- `notes.txt` — goal model, voice brief, architecture decisions, risks; `CHANGELOG.md` — one entry per push

Personal project, single user, no auth. Not a product.
