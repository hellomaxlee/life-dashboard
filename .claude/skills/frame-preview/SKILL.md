---
name: frame-preview
description: Render a fixture day to a 64x64 PNG at 1x and 8x, pass it through the LED-gamma emulator, and screenshot the LAN preview page. Use when the user says "preview the frame", "show me the display", "render today", or after ANY change under app/render/ or to a layout, font, or palette. Do NOT sign off on legibility from the 8x view.
---

# frame-preview

The eye test every display change must pass. Run for a NEW fixture combo each cycle (see CLAUDE.md § Iteration Rule).

1. **Pick the combo** and name it: day type × data completeness × streak state × season. Check the CHANGELOG for the last one used and pick a different cell.
2. **Render:** `uv run python -m tools.render --fixture fixtures/days/<combo>.json --out data/preview/<combo>` produces `frame_1x.png`, `frame_8x.png`, and `frame_gamma_1x.png` (the emulator output).
3. **Snapshot test:** `uv run pytest tests/render -q`. A changed snapshot is reviewed pixel by pixel, never regenerated blindly.
4. **Look at 1x through gamma.** Open `frame_gamma_1x.png` at native size. Ask: what is the one thing this frame says; can it be read in two seconds; does it still read at night brightness. Record the answers in the CHANGELOG bullet.
5. **Preview page:** with the API running, screenshot `http://<host>:8080/preview?date=<date>` via `/playwright-cli` at 1x and 8x. Attach or describe.
6. **Missing-value pass:** render the same combo with one source removed and confirm the layout degrades to a stated fallback, not a blank or a misaligned glyph.
7. **Device (when one exists):** push the frame through the adapter, photograph from three meters, and compare to the emulator output. A mismatch is an emulator bug to fix first.
