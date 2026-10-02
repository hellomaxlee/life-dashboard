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

Which screens animate: Books always (the summary scrolls) unless the line fits the width;
Week only when the Claude reading is stale (pulsing dot, label alternating with its age);
Today never; both celebrations always.

## Fixtures

`fixtures/days/<day type>__<completeness>__<streak state>__<season>.json`. `daily_metrics` and
`weekly_metrics` have the same keys as the database rows (listed in `app/render/view.py`);
`now_utc` fixes the clock and `as_of_utc` is the last Health push. A key left out is a missing
value and must render as a stated fallback. Use a new cell each cycle (CLAUDE.md § Iteration Rule).

## The eye test

1. Render a new combo.
2. Open `frame_gamma_1x.png` for every screen at native size. Write down what each says in two
   seconds. Do not sign off from an 8x file.
3. `uv run pytest tests/render -q`. A changed snapshot is reviewed pixel by pixel; only then
   `UPDATE_SNAPSHOTS=1 uv run pytest tests/render/test_snapshots.py -q` and commit the PNGs.
4. Prove the bar gate still bites: `USAGE_BAR_MUTANT_REMAINING=1 uv run pytest tests/render -q`
   must fail.
5. With the API running, open `http://127.0.0.1:8080/preview?fixture=<combo>` or
   `/preview?date=YYYY-MM-DD`; it shows every screen as LED gamma 1x, LED gamma 8x, raw 1x, raw 8x.

## Device

The Pixoo adapter (`app/render/adapters/pixoo.py`) is unverified on hardware and is called by
nothing. `[device] pixoo_host` in `config.toml` is empty, which means disabled. The emulator's
curve (linear PWM, no firmware gamma) is an assumption until a photograph of a real panel
replaces it.
