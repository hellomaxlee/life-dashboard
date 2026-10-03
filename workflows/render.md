# render — frames, clips, and the eye test

Owner: Diego Almeida. QA: Lucía Ferrer. Tool: `tools/render.py`. Skill: `/frame-preview`.

## What it does

Renders the three rotation screens (Week, Today, Books + summary) and the two celebration
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
<out>/week/      <out>/today/      <out>/books/      <out>/sparkle/      <out>/party/
```

Each folder holds:

| File | What |
|---|---|
| `frame_1x.png` | the 64x64 frame the device would get (a clip's poster frame) |
| `frame_8x.png` | the same, enlarged, for inspecting pixels |
| `frame_gamma_1x.png` | through the LED gamma emulator at native size: **the one to judge** |
| `frame_gamma_8x.png` | emulator output enlarged |
| `clip_1x.gif`, `clip_8x.gif`, `clip_gamma_1x.gif`, `clip_gamma_8x.gif` | only when the clip has more than one frame, with each frame's own duration |

Which screens animate: Books when the summary needs more than one page (two word-wrapped
lines per page, 2 s each; a 110-character line is about seven pages); Week only when the
Claude reading is stale (pulsing dot, label alternating with its age); Today never; both
celebrations always. No clip may exceed 59 frames, the most one device animation is assumed
to hold; a test holds every fixture to it and the Pixoo adapter refuses a longer clip.

What the screens say when data is thin. Today's dot label states the dot, not the day:
"WORKOUT DONE" or "DOT NO DATA", and a day without a dot says why from its own row: "NO
WORKOUT" (`workout_count` 0), "NO HR DATA" (a workout with no `workout_load`), or "LOAD
62/100" (`workout_load` floored against the week's `load_bar`); "NO DOT YET" only when the
row carries none of those. The Week screen reads top to bottom: dots, the weeks-hit streak
under them, the Claude usage bar at the bottom. The as-of line shows the clock for a
same-day push, weekday and clock up to six days back, and whole days beyond that ("AS OF 8D
AGO"); a push dated after the day or after "now" is ignored. A day that has numbers but no
push before it ended (a later push back-filled it) gets no as-of line; "NO PUSH YET" appears
only when the day has no data at all. The summary is folded to ASCII before paging: accents
folded, curly quotes and dashes straightened, paired `*`/`_` markers removed, emoji dropped,
and a number is kept on the same line as its unit or its "of N".

The tool and the preview page always render both celebrations so they can be looked at. One
the day's data did not earn is labelled `(sample)`; an earned party prints the week's stored
count and target.

## Fixtures

`fixtures/days/<day type>__<completeness>__<streak state>__<season>.json`. `daily_metrics` and
`weekly_metrics` have the same keys as the database rows (listed in `app/render/view.py`);
`now_utc` fixes the clock and `as_of_utc` is the last Health push. An optional `note` is for
the reader (two audit fixtures use it to say their summary lines are display data, never
summary-gate goldens). A key left out is a missing
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
job runs it (Week → Today → Books, each held for `device.screen_seconds` or the clip's own
length if longer, the clip looping while held). `/preview` stays the engineering view; the
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
marked; a button plays either on the panel; an earned one plays once when the page loads.
`#screen=today` in the URL starts the rotation on that screen (and skips the auto-play) so a
screenshot can target one screen; `#paused` holds it.

`tools/pixoo_window.py` draws the same look in a native window with stdlib tkinter
(`python -m tools.pixoo_window --url http://<service>:8080 --fixture <combo>`; keys: space
pause, n next, s sparkle, p party, q quit). It reads the service's rotation JSON and frames,
so it shows what the page shows.

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
