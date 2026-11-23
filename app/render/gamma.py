"""LED gamma emulator: what a frame looks like on the panel, shown on an sRGB monitor.

The curve. A monitor shows an 8-bit value v at a luminance of about (v/255)^2.2. An LED
matrix dims by pulse width, which is linear: v is lit v/255 of the time, so luminance is
(v/255)^panel_gamma with panel_gamma = 1.0 unless the firmware corrects it. To show that
luminance on a monitor the pixel has to be 255 * luminance^(1/2.2). With the default
panel_gamma = 1.0 the net exponent is 1/2.2, which lifts dark tones hard: a track drawn at 34
looks like 102, and two dim greys that differ on a monitor merge on the panel.

Why 1.0. The Pixoo-64 (on the LAN since 2026-10-04) has a firmware gamma nobody has
measured. Linear PWM is
the harsher case for a layout that leans on dim tones, so it is the default; if the firmware
does correct to 2.2 the panel matches the raw frame, and the preview shows both. Legibility
is judged on this output at 1x, never on the raw frame at browser zoom.

`brightness` models the device's night dimming as a scale on the 8-bit PWM level before the
panel curve, so values that fall below one PWM step go fully dark, as they do on hardware.
Unverified on hardware; step 7 of the frame-preview skill replaces these assumptions with a
photograph.
"""

from __future__ import annotations

from app.render.frame import Frame

PANEL_GAMMA = 1.0
MONITOR_GAMMA = 2.2


def led_lut(
    panel_gamma: float = PANEL_GAMMA, brightness: float = 1.0, monitor_gamma: float = MONITOR_GAMMA
) -> list[int]:
    if not 0.0 <= brightness <= 1.0:
        raise ValueError("brightness is a fraction between 0 and 1")
    lut = []
    for value in range(256):
        pwm_level = int(value * brightness)
        luminance = (pwm_level / 255) ** panel_gamma
        lut.append(round(255 * luminance ** (1 / monitor_gamma)))
    return lut


def led_gamma(frame: Frame, panel_gamma: float = PANEL_GAMMA, brightness: float = 1.0) -> Frame:
    """Map a frame to how it looks on LEDs. Same size, same mode, never mutates the input."""
    return frame.point(led_lut(panel_gamma, brightness) * 3)
