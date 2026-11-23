# fixtures/city

Replies of the city panel's four public feeds, for `tests/city/` and
`uv run python -m tools.city --fixture fixtures/city/<dir> [--now YYYY-MM-DDTHH:MM]`.
Each directory holds `subway.json`, `bus.json`, `wx.json`, `nws.json`.

## real/ — fetched 2026-10-04 about 23:00 New York

| File | From | Trimmed? |
|------|------|----------|
| `subway.json` | MTA subway alerts feed (feed timestamp 22:56:07) | yes, see below |
| `bus.json` | MTA bus alerts feed (feed timestamp 23:00:23) | yes, see below |
| `wx.json` | Open-Meteo forecast for 40.77, -73.94 | no, whole and byte-identical |
| `nws.json` | National Weather Service active alerts for the point | no, whole; it has no alerts |

What was trimmed from the two MTA files (843 KB and 405 KB as fetched):

- **Alerts.** Kept every alert whose `informed_entity` names a configured line (N, W, M, F:
  30 of 167 subway alerts; Q103, Q66, Q69, B62: 3 of 185 bus alerts, none of them for Q103
  or Q69) plus 12 others per feed, chosen to cover every `alert_type` in the feed and, for
  the bus feed, both alerts that carry no `active_period`. Feed order is kept.
- **Two keys** inside `transit_realtime.mercury_alert`: `affected_stations` and
  `station_alternative` (station lists, 60 KB, never read by the parser). Every other key of
  every kept alert is as fetched, including `description_text` and all active periods.
- **Layout.** One alert per line; the JSON values are unchanged.

Nothing was edited. What the files parse to at 2026-10-04 23:00 and at Monday 08:30 is pinned
in `tests/city/test_parse_transit.py`.

Bus route ids in the feed are bare (`Q66`, `B62`), with the operator in `agency_id`
(`MTABC`, `MTA NYCT`). No `MTABC_Q66` style id appears anywhere in either feed.

## synthetic/ — written by hand, NOT real MTA or NWS messages

Cases tonight's feeds lack. Shapes copy the real files; ids start `synthetic:` and each
description says so. Times are built around 2026-10-04 23:00 New York.

| File | Alert | Case |
|------|-------|------|
| `subway.json` | `synthetic:1` N, Delays, open-ended from 22:40 | delays in effect now |
| | `synthetic:2` N and W, Planned - Stops Skipped, through Monday 05:00 | a second alert on a line; one alert on two lines |
| | `synthetic:3` W, Planned - Suspended, Friday 23:30 to Monday 05:00; header has an em dash | suspended in effect now; non-ASCII folding |
| | `synthetic:4` M, Planned - Reroute, Monday 23:45 only | an alert whose only period is tomorrow |
| | `synthetic:5` F, "Shuttle Buses Replace Trains" | an unknown `alert_type` |
| | `synthetic:6` F, Station Notice about an elevator | an ignored notice, most recently updated |
| | `synthetic:7` M, Planned - Suspended, ended Sunday 21:00 | work that ended earlier today |
| `bus.json` | `synthetic:11` Q69, Detour, open-ended | a bus detour in effect now |
| | `synthetic:12` Q103, Planned - Detour, no `active_period` key | an alert with no active period |
| | `synthetic:13` B62, Boarding Change | an ignored notice |
| | `synthetic:14` Q66, Planned - Stops Skipped, Monday 09:00 to 15:00 | tomorrow only |
| `nws.json` | one "Wind Advisory", Moderate, onset Sunday 18:00, ends Monday 06:00 | a weather alert |
| `wx.json` | a copy of `real/wx.json` | (real) |

The `nws.json` feature's property names were checked against a real alert from the service
on 2026-10-04 (`onset`, `ends`, `effective`, `expires`, `severity`, `event` among them); its
values are invented.
