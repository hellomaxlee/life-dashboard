# render — frames, clips, and the eye test

Owner: Diego Almeida. QA: Lucía Ferrer. Tool: `tools/render.py`. Skill: `/frame-preview`.

## What it does

Renders the five rotation screens (Today, City, Week, Month, Books + summary) and the two celebration
clips (sparkle, party) to files, raw and through the LED gamma emulator, at 1x and enlarged.
Nothing is sent to a device and nothing leaves the machine.

## Commands

```sh
# a fixture day (the test path; "now" comes from the fixture file)
uv run python -m tools.render --fixture fixtures/days/<combo>.json --out data/preview/<combo>

# a real day from the database ("now" is the clock; override with --now)
uv run python -m tools.render --date 2026-10-02 --scale 8
uv run python -m tools.render --date 2026-10-02 --now 2026-10-02T16:00:00Z --out data/preview/x
```

`--out` defaults to `data/preview/<combo or date>`. `--scale` (default 8, minimum 2) names the
enlarged files; the 1x files are always written.

## Files written

One folder per screen, in rotation order, then the celebrations:

```
<out>/today/    <out>/city/    <out>/week/    <out>/month/    <out>/books/    <out>/sparkle/    <out>/party/
```

Each folder holds:

| File | What |
|---|---|
| `frame_1x.png` | the 64x64 frame the device would get (a clip's poster frame) |
| `frame_8x.png` | the same, enlarged, for inspecting pixels |
| `frame_gamma_1x.png` | through the LED gamma emulator at native size: **the one to judge** |
| `frame_gamma_8x.png` | emulator output enlarged |
| `clip_1x.gif`, `clip_8x.gif`, `clip_gamma_1x.gif`, `clip_gamma_8x.gif` | only when the clip has more than one frame, with each frame's own duration |
| `page_<n>_1x.png`, `page_<n>_gamma_1x.png` | only for a paged screen (City, Month with a note, Books): every page as its own still, so each can be judged at 1x |

Which screens animate: Books when the summary needs more than one page (two word-wrapped
lines per page, 2 s each; a 110-character line is about seven pages); Week never (a stale
Claude reading is a still: amber dot, amber age line in place of the reset line); Today never; City is always paged (weather 6 s, lines 6 s, then 5 s for each of up to three
affected lines) unless there is no city status, which is one still; Month is
two pages (the plate 6 s, the note 5 s) only when the day's plate has a note; both
celebrations always. No clip may exceed 59 frames, the most one device animation is assumed
to hold; a test holds every fixture to it and the Pixoo adapter refuses a longer clip.

What the screens say when data is thin. Today's dot label states the dot, not the day:
"WORKOUT DONE" or "DOT NO DATA", and a day without a dot says why from its own row: "NO
WORKOUT" (`workout_count` 0), "NO HR DATA" (a workout with no `workout_load`), or "LOAD
62/100" (`workout_load` floored against the week's `load_bar`); "NO DOT YET" only when the
row carries none of those. "NO WORKOUT" needs a day a push has covered (it has sleep or steps); the row the
engine writes before any push says nothing. The Week screen reads top to bottom: dots, the
weeks-hit streak under them, the Claude usage bar at the bottom.

Health pushes cover whole days ending yesterday, so today has no sleep, steps or workout
until tomorrow. When the requested day has none and the day before does, the Today screen
shows the day before and is headed "YESTERDAY FRI 2" instead of "TODAY SAT 3". Week, streak,
books, summary and Claude never fall back.

`/preview` and `/pixoo` with no `date` or `fixture` show a placeholder fixture under an amber
"Placeholder data" banner when the database has no Health data for today or yesterday, and the
database as soon as it does; `?placeholder=0` always shows the database. The frames a device
is sent (`/pixoo/rotation.json`, the rotation job) never use the placeholder. The as-of line shows the clock for a
same-day push ("AS OF 1:30PM"; 12-hour, never 24-hour, Max 2026-10-08), weekday and clock
up to six days back ("AS OF TUE 1:30P", the one-letter suffix because the full one is wider
than the frame at 10 to 12 o'clock), and whole days beyond that ("AS OF 8D AGO"); a push dated after the day or after "now" is ignored. A day that has numbers but no
push before it ended (a later push back-filled it) gets no as-of line; "NO PUSH YET" appears
only when the day has no data at all. The summary is folded to ASCII before paging: accents
folded, curly quotes and dashes straightened, paired `*`/`_` markers removed, emoji dropped,
and a number is kept on the same line as its unit or its "of N".

Colour (approved 2026-10-05). Each rotation screen's header word wears its own accent from
`palette.HEADERS`: `TODAY`/`YESTERDAY` violet, `WEEK` coral, `BOOKS` gold; the right-hand stamp
(weekday and date, `0 OF 3`, the year) stays TEXT, and the small field labels inside a screen
(`SLEEP`, `STEPS`, `CLAUDE`, `WK STREAK`) stay LABEL blue. City keeps its LABEL header and Month
its feature palette. On the shelf, an unread slot is the spine colour it will take when read at
`SHELF_TINT` (0.18): through the panel curve every tint is under half the lit spine's luma
(worst 0.46), keeps its hue (HSV saturation at least 0.32), and sits at about the old grey
track's weight, so the shelf reads as colour waiting to fill, not as lit. The summary is painted
in three voices by character (Max, 2026-10-05: the quotation in white, the author in gold,
his own clause in a third colour): a double-quoted quotation, marks included, in TEXT; the
`- Author` after a closing mark in GOLD to the end of its clause; and Max's own words around
the quote in `VOICE` cream (255, 212, 120; (255, 234, 181) on the panel, midway in warmth
between TEXT and GOLD). Colours are assigned on the wrapped text and drawn as runs, so a
quotation that crosses a line or a page keeps its colour on each; a summary with no quotation,
or an odd number of marks (a quotation the 220-character cut ran through), is all TEXT as
before.

The tool and the preview page always render both celebrations so they can be looked at. One
the day's data did not earn is labelled `(sample)`; an earned party prints the week's stored
count and target.

## The Month screen

`app/render/month.py`. With a stored feature for the requested day's month (`app/month/`, one
authored theme a month, one 16x16 plate a day): the title in the small face in the palette's
first colour, the day's plate at three LEDs a cell on black, the caption in the second colour,
and a rail of one pip per day down each side (days gone in a dimmed second colour, today white,
days to come in the track grey). A day with a note gets a second page: the same title over the
note in the body face, word-wrapped and centred. Text that would not fit is cut at a whole
glyph, and a note too long for the body face drops to the small one; nothing is drawn off the
frame. Title and caption colours too dark for small text are lifted toward white.

The last page, every month, is the calendar (Max, 2026-10-07: "I really like this panel -
can we have this every month?"), drawn from the date alone: month name in a colour of its own
and the year, `M T W T F S S`, one 6x6 cell per day in Monday-first weeks, past days in the
month's colour dimmed, today white, days to come grey, up for `CALENDAR_MS` (6 s). No metric
is read. With no feature for that month (none generated yet, the model unavailable or over
budget, a stored row that no longer parses, the table missing) the calendar is the whole
screen, a still held for the dwell.

The month is always the requested day's. When Today falls back to yesterday on the first of a
month, the Month screen still shows the new month.

## The City screen

`app/render/city.py`, drawn from the view's `city` (`app/city/model.py`; `view_db` reads it with
`app.city.store.load_status` for the requested day). `--date` does not back-date it: the store
keeps only the latest transit and weather snapshot, and the day picks which weekday's alert
lines apply, so a past date renders today's city on that weekday. Pages:

- **weather**: condition icon, the current temperature large, `H`/`L`, one notice line (the
  first NWS alert in amber, wrapped to a second line in place of the step temperatures when it
  is long; otherwise the condition in words), and six 3-hour steps: temperature, a small
  icon, a rain gauge and the hour. Steps past midnight are in the as-of colour behind a dotted
  rule. Temperatures are rounded half up; a gauge is `floor(percent * 10 / 100)` of 10 px.
- **lines**: one row per line: a badge in the MTA colour (disc for a subway, blue pill for a
  bus), the name in white beside it, and the status as a word in a colour: `OK` green, `WORK`
  amber (a clock mark when it starts later today), `DELAYS` red, `NO SVC` red. Seven lines
  use the body face for the name; eight use a tighter row in the small face; more than eight
  end in `+N MORE`.
- **detail**, at most three: one per line that is not ok, most severe first, then in effect
  now before later: that line's row as the header and the alert's headline wrapped under it
  (body face, then small face, then cut with `...`). More affected lines than pages: the last
  page ends `+N MORE`.

All ok with no alert is two pages. A part never fetched says `WEATHER NO DATA` or `TRANSIT NO
DATA` on its page. A part older than `[city] stale_minutes` (45) replaces its page's header
with an amber `AS OF 2:05PM` (weekday and clock, `AS OF TUE 2:05P`, for earlier days, whole days past six; `AGE
UNKNOWN` when the time cannot be read), and detail pages carry it on their last line. No city
status at all is one still: `CITY`, the weekday and date, `NO DATA`. Every city page keeps its
header on row 2 in the small face.

## Fixtures

`fixtures/days/<day type>__<completeness>__<streak state>__<season>.json`. `daily_metrics` and
`weekly_metrics` have the same keys as the database rows (listed in `app/render/view.py`);
`now_utc` fixes the clock and `as_of_utc` is the last Health push. An optional `note` is for
the reader (two audit fixtures use it to say their summary lines are display data, never
summary-gate goldens). An optional `month_feature` names a file under `fixtures/month/`
(`sample-2026-10`, a hand-made moon over water); the loader re-dates that sample to the
fixture's own month, cutting its days to the month's length, so one sample serves any fixture
day. A fixture without the key renders the calendar. An optional `city` names a file under
`fixtures/city_view/` (`busy`, `all-ok`, `storm`, `stale`, `weather-only`: hand-written
display samples, not the raw payloads under `fixtures/city/`); the loader dates it to the
fixture's day and counts its `*_minutes_ago` back from the fixture's `now_utc`. A fixture
without the key renders `CITY NO DATA`. A key left out is a missing
value and must render as a stated fallback. Use a new cell each cycle (CLAUDE.md § Iteration Rule).
Every file in the folder is rendered and snapshot-tested, so a new fixture needs its goldens:
render it, look at it (next section), then run the `UPDATE_SNAPSHOTS=1` command and commit them.
Until then `tests/render/test_snapshots.py` fails for that fixture by design; nothing else does.

## The eye test

1. Render a new combo.
2. Open `frame_gamma_1x.png` for every screen at native size. Write down what each says in two
   seconds. Do not sign off from an 8x file.
3. `uv run pytest tests/render -q`. A changed snapshot is reviewed pixel by pixel; only then
   `UPDATE_SNAPSHOTS=1 uv run pytest tests/render/test_snapshots.py -q` and commit the PNGs.
4. Prove the bar gate still bites: `USAGE_BAR_MUTANT_REMAINING=1 uv run pytest tests/render -q`
   must fail. The golden case: 41.2 % fills `floor(0.412 * 60) = 24` px of the 60 px track.
5. With the API running, open `http://127.0.0.1:8080/preview?fixture=<combo>` or
   `/preview?date=YYYY-MM-DD`; it shows every screen as LED gamma 1x, LED gamma 8x, raw 1x, raw 8x.
6. For the desk view, open `/pixoo?fixture=<combo>` (next section) and watch one full rotation.

## The device page

`GET /pixoo` is the display as a Pixoo-64 on a desk: a dark bezel, a 64x64 matrix drawn as
round LEDs with black gaps and a soft glow, and the rotation running live the way the device
job runs it: Today → City → Week → Month → Books → one sparkle per small win the day earned
(workout, sleep, book) → the week-complete party once the week's target is met, repeating
(day, the day's city, week, month, year, then the wins). `app.render.rotation.sequence_names` is the one
definition; a restart begins at Today. A still holds for `device.screen_seconds` (6 s). Anything animated holds
for whole plays and is never replaced part way: Books until its summary has paged through
exactly once (2 s a page), City's pages once each (6 s, 6 s, then 5 s a detail page), Month's plate 6 s then its note 5 s, each sparkle one pass
of five stills at 300 ms (about 9 s on the device, each still's 1.45 s upload included), the party one pass of six stills at 300 ms (about 10 s). On the device a paged screen goes as one still per page. The workout sparkle reads "WORKOUT DONE / SMALL WIN", and once the week's target is met
it stays in the sequence every day through Sunday, and so does the party. `/preview` stays the engineering view; the
three pages link each other on their first line.

Rendering never moves into the browser. The page fetches `/pixoo/rotation.json` (screens in
order, one URL per frame with its duration, `hold_ms`, the as-of time, and the lookup tables)
and each frame as raw 64x64 PNG bytes from `/pixoo/frame/<screen>/<index>.png`, then maps
pixels to LEDs on a canvas with inline script: one disc per pixel, 70 % of the cell wide, over
faint unlit dots, with a glow made by drawing the 64x64 frame smoothed up to panel size at
55 % in additive mode under the discs. No external scripts, fonts, or assets.

Controls: date (defaults to today in the home timezone) or fixture, size 256/512/768 px,
brightness 10 to 100 %, LED gamma on/off (default on), pause, next screen. Brightness and
gamma are client-side: the server ships `led_lut(brightness=b)` for every step and the raw
`int(v * b)` table beside it, so dimming scales the PWM level before the panel curve exactly as
`led_gamma(frame, brightness=b)` does, and changing either redraws from cached pixels without
another request. The strip under the device shows both celebrations with earned or sample
marked; a button plays either on the panel. The page starts at Today, as the device does.
`#screen=month` (or `today`, `city`, `week`, `books`, `win-sleep`, `party`) in the URL starts the
rotation on that screen so a screenshot can target one; a celebration the day did not earn
is played once as a sample instead. `#paused` holds it.

The tkinter window that drew the same look natively (`pixoo_window.py`, with its test) is
archived in `_old/` since 2026-10-07: the real panel has been on the LAN since 2026-10-04 and
the `/preview` page covers the rest.

What the LED look cannot settle: the glow strength, dot size and unlit grey are a guess at a
panel nobody here has photographed; they are display chrome, not the emulator. Legibility is
still judged on `frame_gamma_1x.png` and on the device.

## Device

The Pixoo adapter (`app/render/adapters/pixoo.py`) is unverified on hardware and is called only
by the `device_rotation` job, which is not registered while no host is set (`workflows/run-service.md` section 15). `[device] pixoo_host` in `config.toml` is empty, which means disabled.
Its HTTP client ignores proxy settings in the environment, so frames go to the LAN address only.

To verify on hardware:
- the emulator's curve (linear PWM, no firmware gamma), against a photograph of the panel;
- the 3x5 label font: several digits differ by a single LED (0/8, 5/6, 3/9, 6/8). They read
  in the emulator; whether they read on real LEDs from across the room is undecided until a
  photo, and the font stays as it is until then;
- the frame limit, per-frame speed and command names of the device's HTTP API.
