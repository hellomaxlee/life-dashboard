# city — transit status and weather

Owner: Bartek Zieliński (data); Lucía Ferrer (the screen, `app/render/city.py`).
Tool: `tools/city.py`. Code: `app/city/` (`model.py` the contract, `fetch.py` the outbound
calls and the two polls, `parse.py` pure parsers, `store.py` the snapshot table).

## What it does

Every few minutes it reads the MTA's service alerts for the lines Max rides and the day's
weather for Astoria, and stores one parsed snapshot of each. The renderer asks
`app.city.store.load_status(conn, day_local)` for a `CityStatus`: the lines that apply that
day, each "ok", "planned", "delays" or "suspended" with the top alert's own headline; the
weather now, the day's high and low, six 3-hour steps, and active weather alerts.

Nothing here is a metric and nothing here touches health data.

## Commands

```sh
uv run python -m tools.city                    # fetch both now, store, print the day's status
uv run python -m tools.city --show             # print what is stored; nothing is fetched
uv run python -m tools.city --fixture fixtures/city/real --now 2026-10-04T23:00
                                               # parse saved replies; no network, no database
```

Exit 0 when printed; 1 when a fetch failed (the last good snapshot is still printed) or
nothing is stored; 2 when refused (schema mismatch, bad arguments). The first two use
`connect_live`: after pulling migration 007 the service must be restarted (it migrates at
start) before they will open the live db.

## Outbound calls (the whole list)

| Host | Request | What it carries |
|------|---------|-----------------|
| `api-endpoint.mta.info` | GET `/Dataservice/mtagtfsfeeds/camsys%2Fsubway-alerts.json` and `…camsys%2Fbus-alerts.json` | nothing |
| `api.open-meteo.com` | GET `/v1/forecast` | latitude and longitude rounded to two decimals, the home timezone name, the list of fields |
| `api.weather.gov` | GET `/alerts/active?point=LAT,LON` | the same rounded point |

All three are public and need no key. Every request sends the fixed header
`User-Agent: life-dashboard (personal use)`: no name, no email. No health data, no metric and
no address is ever in a request. `fetch.coordinate` formats the point to two decimals at the
moment of sending and `config.py` refuses a `[city]` latitude or longitude with more.

Each host is pinned the way Goodreads is, in `fetch.get_json`: https only; host in
`fetch.ALLOWED_HOSTS`, checked before any I/O; any redirect refused, whatever its target;
`trust_env=False`, so proxy variables are ignored; 5 s to connect, 10 s per read, 25 s in
all; the reply is read no further than 4 MB (tonight's largest, the subway feed, is 0.85 MB).
`tests/test_egress.py` holds the same three hosts in its allowlist and fails if the code's
list and the gate's ever differ.

## Decisions

- **Config `[city]`.** `enabled`; `latitude = 40.77`, `longitude = -73.94` (Astoria, rounded
  on purpose to about a kilometre; the street address is in no file and no request);
  `subway = ["N", "W"]` every day, `subway_weekday = ["M"]`, `subway_weekend = ["F"]`
  (Saturday and Sunday in the home timezone are the weekend), `bus = ["Q103", "Q66", "Q69",
  "B62"]`; `transit_poll_minutes = 5`, `weather_poll_minutes = 30`, `stale_minutes = 45`.
  Types and ranges are checked at boot. `LIFE_CITY_ENABLED=0` switches the panel's jobs off
  (the test suite sets it, so no test can reach the network through the scheduler).
- **Not archived.** The feeds are display-only and about 1.2 MB a poll, so they are not
  written to `data/raw`. "Raw before parsed" covers payloads that metrics are computed from;
  no metric reads these.
- **One table, two rows.** Migration 007, `city_snapshots`: the latest parsed transit
  snapshot and the latest parsed weather snapshot as JSON with `fetched_at_utc`, replaced on
  each successful fetch. A failed fetch, or a 200 reply that is not the feed it should be,
  changes nothing: the last good snapshot stays and ages. Whether a snapshot is too old to
  show is the renderer's call; it gets both fetch times and `stale_minutes`.
- **Replay and backup.** The table is neither ingested data, nor derived, nor authored. It is
  in none of `tools.replay`'s table lists, so `--verify` and `tools.backup --verify` do not
  compare it, `--rebuild-live` leaves it alone, and the backup checksum does not cover it. A
  backup's db copy still contains it; after a restore it is simply an old snapshot until the
  next poll.
- **The transit snapshot covers every configured line** (weekday and weekend ones alike) and
  records the config groups, so `load_status` picks the day's lines from the stored row.

## How a line gets its status

Only alerts that name the line count, and only those in effect at some point between now and
the end of today (home timezone). An alert with no active period counts as in effect. Work
that ended earlier today is over and is not shown.

| `alert_type` | Status |
|--------------|--------|
| Suspended, Planned - Suspended, No Scheduled Service | "suspended" while in effect now; "planned" when it starts later today |
| Delays, Expect Delays, Some/Severe Delays, Reduced Service, Slow Speeds | "delays" while in effect now; "planned" when later today |
| any other "Planned - …", Detour, Part Suspended, Stops Skipped, Express to Local, Reroute, Special Schedule, Service Change | "planned" |
| Station Notice, Boarding Change, Extra Service, anything about an elevator or escalator | ignored: never counted, never shown |
| anything else | "planned", and the type is logged once per process |

The line shows its most severe alert ("suspended" over "delays" over "planned"); a tie goes
to an alert in effect now, then to the most recently updated. `headline` is that alert's
English plain header with the "[F]" bullets kept, folded to ASCII on one line, at most 120
characters, cut at a word with "...". `alerts` counts the line's non-ignored alerts.

Route ids: both feeds use bare ids ("N", "Q66", "B62"). A prefixed id ("MTABC_Q66") would
match on the part after the last underscore; a subway express id ("FX") is its line; a
Select Bus id ("Q52+") also matches its plain number. Subway lines read only the subway
feed and buses only the bus feed, so the M train never picks up an M15 bus alert.

## Weather

- Now, feels-like and code from `current`; high, low and precipitation chance from the
  `daily` entry for today's local date.
- `hours`: six steps three hours apart starting at the current local hour. Late in the day
  they run into tomorrow (`tomorrow = True`). An hour the reply does not cover is left out.
- `alerts`: National Weather Service event names in effect at some point between now and the
  end of today, most severe first (Extreme, Severe, Moderate, Minor, Unknown), no repeats,
  at most three. The service lists an advisory well before its onset: one that starts this
  afternoon is shown from the morning, one that starts tomorrow is not.
- If the forecast arrives and the alerts call fails, the forecast is stored anyway with the
  previous alerts while those are younger than `stale_minutes` (none after that), and the
  job logs the failure.

## When it runs by itself

Jobs `city_transit` (every `transit_poll_minutes`, first run 20 s after start) and
`city_weather` (every `weather_poll_minutes`, first run 30 s after start), registered only
when `[city] enabled`. Misfire grace is one interval. Neither can raise into the scheduler.
A network or feed failure is one log line per distinct error, not one per poll:
`job city_transit failed: FetchError: … (repeats are not logged)`. Anything else (a bug) is
logged with its traceback.

## Checking it

```sh
uv run pytest tests/city tests/jobs/test_scheduler.py tests/test_egress.py
uv run python -m tools.city --fixture fixtures/city/real --now 2026-10-04T23:00
```

`fixtures/city/README.md` says which fixture files are real, what was trimmed and which are
synthetic.
