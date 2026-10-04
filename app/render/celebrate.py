"""Two celebration clips: the small-win sparkle and the week-complete party.

Wins are celebrated on the device; nothing here is ever shown for a miss. Both clips are
deterministic (a seeded generator, no clock), so they snapshot-test like any still frame.

Sparkle: the win's own icon pops in the centre while four-point stars twinkle around it.
Party: the frame opens as the Week screen's dot row with the last dot missing; that dot drops
in and bounces, all dots flash, then they throw confetti while "WEEK DONE" rides a rainbow wave.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Literal

from app.render.font import BODY, SMALL, draw_text, draw_text_centered, text_width
from app.render.frame import SIZE, Clip, Color, Frame, new_frame
from app.render.palette import CONFETTI, GOLD, LABEL, TEAL, TEXT, VIOLET, WHITE, dim, hue
from app.render.screens import (
    DOT_ROW_Y,
    MOON,
    dot_color,
    dot_layout,
    draw_bitmap,
    draw_disc,
    draw_ring,
    fill_rect,
    sleep_met,
)
from app.render.view import DayView, valid_count

Win = Literal["sleep", "workout", "book"]

WIN_LABELS: dict[str, tuple[str, ...]] = {
    "sleep": ("RESTED",),
    "workout": ("WORKOUT DONE",),
    "book": ("BOOK DONE",),
}
WIN_ORDER: tuple[Win, ...] = ("workout", "sleep", "book")
SPARKLE_FRAMES = 20
SPARKLE_FRAME_MS = 70
PARTY_FRAMES = 56
PARTY_FRAME_MS = 60
_DROP_FRAMES = 12
_FLASH_FRAMES = 4
_GRAVITY = 0.16


def _put(frame: Frame, x: int, y: int, color: Color) -> None:
    if 0 <= x < SIZE and 0 <= y < SIZE:
        frame.putpixel((x, y), color)


def draw_twinkle(frame: Frame, x: int, y: int, size: int, color: Color) -> None:
    """A four-point star: size 0 is a pixel, 1 a plus, 2 a plus with long arms and a white core."""
    if size < 0:
        return
    _put(frame, x, y, WHITE if size == 2 else color)
    for arm in range(1, size + 1):
        shade = color if arm == 1 else dim(color, 0.55)
        for dx, dy in ((arm, 0), (-arm, 0), (0, arm), (0, -arm)):
            _put(frame, x + dx, y + dy, shade)


def _win_icon(frame: Frame, win: str, grow: float) -> None:
    cx, cy = SIZE // 2, 26
    if win == "sleep":
        if grow >= 0.5:
            draw_bitmap(frame, cx - 4, cy - 5, MOON, VIOLET)
        else:
            draw_disc(frame, cx, cy, 2, VIOLET)
    elif win == "book":
        half = max(1, int(7 * grow))
        fill_rect(frame, cx - half, cy - 5, cx - 1, cy + 5, TEAL)
        fill_rect(frame, cx + 1, cy - 5, cx + half, cy + 5, TEAL)
        fill_rect(frame, cx, cy - 5, cx, cy + 6, WHITE)
        if half >= 5:
            for line_y in (cy - 2, cy + 1):
                fill_rect(frame, cx - half + 2, line_y, cx - 2, line_y, dim(TEAL, 0.45))
                fill_rect(frame, cx + 2, line_y, cx + half - 2, line_y, dim(TEAL, 0.45))
    else:
        draw_disc(frame, cx, cy, max(1, int(7 * grow)), GOLD)


def sparkle_clip(win: Win = "workout", seed: int = 11) -> Clip:
    """The small-win clip, about 1.4 s. `win` picks the icon and the label."""
    rng = random.Random(seed)
    lines = WIN_LABELS[win]
    star_floor = 46 - 7 * (len(lines) - 1)
    stars = []
    while len(stars) < 14:
        x, y = rng.randrange(4, SIZE - 4), rng.randrange(4, star_floor)
        if abs(x - SIZE // 2) < 12 and abs(y - 26) < 12:
            continue
        stars.append((x, y, rng.randrange(6), rng.random()))

    frames: list[Frame] = []
    for tick in range(SPARKLE_FRAMES):
        frame = new_frame()
        grow = min(1.0, (tick + 1) / 6)
        overshoot = 1.15 if tick in (5, 6) else 1.0
        _win_icon(frame, win, min(1.0, grow) * overshoot if win == "workout" else grow)
        if 4 <= tick <= 11:
            draw_ring(frame, SIZE // 2, 26, 6 + (tick - 4) * 2, dim(GOLD, 1.0 - (tick - 4) * 0.12))
        for x, y, phase, colour_at in stars:
            step = (tick + phase) % 6
            size = (0, 1, 2, 1, 0, -1)[step]
            draw_twinkle(frame, x, y, size, hue(colour_at + tick * 0.02))
        for row, line in enumerate(lines):
            draw_text_centered(frame, 57 - 7 * (len(lines) - row), line, TEXT, SMALL)
        draw_text_centered(frame, 57, "SMALL WIN", LABEL, SMALL)
        frames.append(frame)
    return Clip(tuple(frames), (SPARKLE_FRAME_MS,) * SPARKLE_FRAMES, poster_index=8)


def _bounce_y(tick: int) -> int:
    """Fall from above the frame to the dot row with two rebounds."""
    t = min(tick / (_DROP_FRAMES - 1), 1.0)
    if t < 0.55:
        height = 1.0 - (t / 0.55) ** 2
    elif t < 0.85:
        u = (t - 0.55) / 0.30
        height = 0.28 * (1 - (2 * u - 1) ** 2)
    else:
        u = (t - 0.85) / 0.15
        height = 0.08 * (1 - (2 * u - 1) ** 2)
    return int(DOT_ROW_Y - height * (DOT_ROW_Y + 8) + 0.5)


def _rainbow_wave(frame: Frame, text: str, y: int, tick: int) -> None:
    x = (SIZE - text_width(text, BODY)) // 2
    for index, char in enumerate(text):
        bob = int(round(1.5 * math.sin(tick * 0.55 + index * 0.9)))
        x = draw_text(frame, x, y + bob, char, hue(index * 0.11 + tick * 0.04), BODY)


def party_clip(count: int = 3, target: int = 3, seed: int = 7) -> Clip:
    """The week-complete clip, about 3.4 s: the last dot lands, then confetti.

    `count` and `target` are the week's stored numbers; the clip prints them as given.
    """
    rng = random.Random(seed)
    centres, radius = dot_layout(target)
    burst_at = _DROP_FRAMES + _FLASH_FRAMES
    particles = []
    for index, cx in enumerate(centres):
        for _ in range(22):
            angle = rng.uniform(math.pi * 1.05, math.pi * 1.95)
            speed = rng.uniform(0.9, 2.6)
            particles.append(
                (
                    float(cx),
                    float(DOT_ROW_Y),
                    math.cos(angle) * speed,
                    math.sin(angle) * speed,
                    rng.choice(CONFETTI),
                    burst_at + index * 3 + rng.randrange(3),
                    rng.randrange(2),
                )
            )
    for _ in range(40):
        particles.append(
            (
                float(rng.randrange(SIZE)),
                -2.0,
                rng.uniform(-0.3, 0.3),
                rng.uniform(0.4, 1.1),
                rng.choice(CONFETTI),
                burst_at + 6 + rng.randrange(30),
                rng.randrange(2),
            )
        )

    frames: list[Frame] = []
    for tick in range(PARTY_FRAMES):
        frame = new_frame()
        last = len(centres) - 1
        for index, cx in enumerate(centres):
            if index < last:
                color = dot_color(index)
                if tick >= burst_at:
                    color = hue(index / len(centres) + (tick - burst_at) * 0.03)
                draw_disc(frame, cx, DOT_ROW_Y, radius, color)
        if tick < _DROP_FRAMES:
            draw_ring(frame, centres[last], DOT_ROW_Y, radius, dim(WHITE, 0.3))
            draw_disc(frame, centres[last], _bounce_y(tick), radius, dot_color(last))
        elif tick < burst_at:
            flash = WHITE if (tick - _DROP_FRAMES) % 2 == 0 else dot_color(last)
            for index, cx in enumerate(centres):
                draw_disc(frame, cx, DOT_ROW_Y, radius, flash if index == last else WHITE)
                draw_ring(frame, cx, DOT_ROW_Y, radius + 2 + (tick - _DROP_FRAMES), GOLD)
        else:
            draw_disc(
                frame,
                centres[last],
                DOT_ROW_Y,
                radius,
                hue(last / len(centres) + (tick - burst_at) * 0.03),
            )

        for x0, y0, vx, vy, color, born, wide in particles:
            age = tick - born
            if age < 0:
                continue
            x = int(x0 + vx * age)
            y = int(y0 + vy * age + 0.5 * _GRAVITY * age * age)
            shade = color if (age + wide) % 4 else WHITE
            _put(frame, x, y, shade)
            if wide:
                _put(frame, x + 1, y, shade)
        if tick >= burst_at:
            draw_text_centered(frame, 31, f"{count} OF {target}", WHITE, SMALL)
            _rainbow_wave(frame, "WEEK DONE", 42, tick)
        frames.append(frame)
    return Clip(tuple(frames), (PARTY_FRAME_MS,) * PARTY_FRAMES, poster_index=burst_at + 14)


CELEBRATION_ORDER = ("sparkle", "party")


@dataclass(frozen=True)
class Celebration:
    name: str
    clip: Clip
    earned: bool


def earned_wins(view: DayView) -> list[Win]:
    """Every small win the view itself shows, in WIN_ORDER: a quality workout, a night at the
    sleep target, a finished book. Once the week's target is met the workout win stays for
    the rest of that week, workout or not."""
    earned = {
        "workout": view.today_dot is True or week_complete(view),
        "sleep": sleep_met(view),
        "book": view.book_finished,
    }
    return [win for win in WIN_ORDER if earned[win]]


def earned_win(view: DayView) -> Win | None:
    """The first earned small win, or None."""
    wins = earned_wins(view)
    return wins[0] if wins else None


def week_complete(view: DayView) -> bool:
    return valid_count(view.week_dots) and view.week_dots >= view.week_target


def celebrations_for(view: DayView) -> list[Celebration]:
    """Both clips for a day, each marked earned or not.

    A preview shows a clip the day did not earn as a labelled sample. An earned party prints
    the week's stored count and target; a sample prints the target twice.
    """
    win = earned_win(view)
    done = week_complete(view)
    count = view.week_dots if done else view.week_target
    return [
        Celebration("sparkle", sparkle_clip(win or "workout"), win is not None),
        Celebration("party", party_clip(count, view.week_target), done),
    ]
