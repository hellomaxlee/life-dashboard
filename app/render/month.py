"""The Month screen: the month's feature for the day, then the month as a calendar.

With a feature (app/month/spec.py) for the day's month the screen is pages that read as one:
the title over the day's 16x16 plate drawn at three LEDs a cell on true black, the caption
under it, and a rail of one pip per day down each side (the days gone, today, the days to
come); then, when the day has a note, the title over the note in the body face; then the
calendar, every day of every month (Max, 2026-10-07: "I really like this panel").

The calendar is drawn from the date alone: the month's name and year, a Monday-first grid
with one cell per day, past days soft, today bright, the days to come dim but there. No metric
and no personal data. Without a feature (none generated yet, the model unavailable or over
budget) it is the whole screen.

The day is always the view's requested day, never `day_shown`: the Today screen may fall back
to yesterday's facts, but the month does not turn back with it.
"""

from __future__ import annotations

import calendar
from datetime import date, datetime

from app.month.spec import ART_SIZE, UNLIT, DayPlate, MonthFeature
from app.render.font import BODY, SMALL, Font, draw_text, text_width
from app.render.frame import SIZE, Clip, Color, Frame, new_frame, still
from app.render.palette import LABEL, TEXT, TRACK, WHITE, dim, hue
from app.render.screens import fill_rect, wrap_lines
from app.render.view import DayView

PLATE_MS = 6000
NOTE_MS = 5000
CALENDAR_MS = 6000

PLATE_SCALE = 3
PLATE_PX = ART_SIZE * PLATE_SCALE
PLATE_X = (SIZE - PLATE_PX) // 2
PLATE_Y = 8
TITLE_Y = 2
CAPTION_Y = 58
TEXT_WIDTH = 62
RAIL_XS = (3, 59)
RAIL_PIP = 2
RAIL_PITCH = 3
RAIL_ROWS = PLATE_PX // RAIL_PITCH
RAIL_PAST = 0.45
MIN_TEXT_LUMINANCE = 0.2

NOTE_TOP = 8
NOTE_BOTTOM = 62
NOTE_WIDTH = 60
NOTE_PITCHES = ((BODY, 9), (BODY, 8), (SMALL, 7))

MONTH_NAMES = (
    "JANUARY",
    "FEBRUARY",
    "MARCH",
    "APRIL",
    "MAY",
    "JUNE",
    "JULY",
    "AUGUST",
    "SEPTEMBER",
    "OCTOBER",
    "NOVEMBER",
    "DECEMBER",
)
WEEKDAY_LETTERS = "MTWTFSS"
CELL = 6
CELL_PITCH = 8
GRID_X = (SIZE - (7 * CELL_PITCH - (CELL_PITCH - CELL))) // 2
HEADER_GAP = 3
CALENDAR_TOP = 8
PAST_SHADE = 0.5


def legible(color: Color) -> Color:
    """The colour, lifted toward white only as far as small text needs to be read on LEDs."""
    luminance = (0.2126 * color[0] + 0.7152 * color[1] + 0.0722 * color[2]) / 255
    if luminance >= MIN_TEXT_LUMINANCE:
        return color
    lift = (MIN_TEXT_LUMINANCE - luminance) / (1 - luminance)
    return tuple(round(c + (255 - c) * lift) for c in color)


def fit_text(text: str, width: int, font: Font = SMALL) -> str:
    """The longest whole-glyph prefix that fits `width` pixels; never a clipped glyph."""
    text = " ".join(text.split())
    while text and text_width(text, font) > width:
        text = text[:-1].rstrip()
    return text


def _centered(frame: Frame, y: int, text: str, color: Color, font: Font = SMALL) -> None:
    text = fit_text(text, TEXT_WIDTH, font)
    draw_text(frame, (SIZE - text_width(text, font)) // 2, y, text, color, font)


def plate_for(view: DayView) -> tuple[MonthFeature, DayPlate] | None:
    """The feature and its plate for the view's requested day, or None when there is no
    feature for that day's month."""
    feature = view.month_feature
    if feature is None:
        return None
    try:
        plate = feature.plate(view.day_local)
    except (IndexError, ValueError):
        return None
    return None if plate is None else (feature, plate)


def draw_plate(frame: Frame, plate: DayPlate, palette: tuple[Color, ...]) -> None:
    """Each art cell as a PLATE_SCALE block of its palette colour; an unlit cell stays black."""
    for row, cells in enumerate(plate.art[:ART_SIZE]):
        for column, cell in enumerate(cells[:ART_SIZE]):
            if cell == UNLIT or not cell.isdigit() or not 1 <= int(cell) <= len(palette):
                continue
            x, y = PLATE_X + column * PLATE_SCALE, PLATE_Y + row * PLATE_SCALE
            fill_rect(frame, x, y, x + PLATE_SCALE - 1, y + PLATE_SCALE - 1, palette[int(cell) - 1])


def rail_pips(days: int) -> list[tuple[int, int, int]]:
    """(day, x, y) of each day's pip: the left rail top to bottom, then the right."""
    return [
        (day, RAIL_XS[(day - 1) // RAIL_ROWS], PLATE_Y + (day - 1) % RAIL_ROWS * RAIL_PITCH)
        for day in range(1, min(days, 2 * RAIL_ROWS) + 1)
    ]


def _draw_rails(frame: Frame, today: int, days: int, palette: tuple[Color, ...]) -> None:
    for day, x, y in rail_pips(days):
        if day == today:
            color = WHITE
        elif day < today:
            color = dim(palette[1 % len(palette)], RAIL_PAST)
        else:
            color = TRACK
        fill_rect(frame, x, y, x + RAIL_PIP - 1, y + RAIL_PIP - 1, color)


def _plate_frame(feature: MonthFeature, plate: DayPlate) -> Frame:
    frame = new_frame()
    palette = feature.palette
    _centered(frame, TITLE_Y, feature.title, legible(palette[0]))
    draw_plate(frame, plate, palette)
    _draw_rails(frame, plate.day, len(feature.days), palette)
    _centered(frame, CAPTION_Y, plate.caption, legible(palette[1 % len(palette)]))
    return frame


def _wrap_small(text: str) -> list[str]:
    lines: list[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}" if current else word
        if text_width(candidate, SMALL) <= NOTE_WIDTH:
            current = candidate
            continue
        if current:
            lines.append(current)
        current = fit_text(word, NOTE_WIDTH)
    if current:
        lines.append(current)
    return lines


def note_layout(note: str) -> tuple[Font, int, list[str]]:
    """(face, line pitch, lines): the body face while the wrapped note fits under the title,
    then tighter, then the small face; lines past the frame are dropped, never drawn off it."""
    room = NOTE_BOTTOM - NOTE_TOP + 1
    for font, pitch in NOTE_PITCHES:
        lines = wrap_lines(note) if font is BODY else _wrap_small(note)
        if (len(lines) - 1) * pitch + font.height <= room:
            return font, pitch, lines
    return font, pitch, lines[: (room - font.height) // pitch + 1]


def _note_frame(feature: MonthFeature, plate: DayPlate) -> Frame:
    frame = new_frame()
    palette = feature.palette
    _centered(frame, TITLE_Y, feature.title, legible(palette[0]))
    font, pitch, lines = note_layout(plate.note)
    height = (len(lines) - 1) * pitch + font.height
    top = NOTE_TOP + (NOTE_BOTTOM - NOTE_TOP + 1 - height) // 2
    color = legible(max(palette, key=sum))
    for index, line in enumerate(lines):
        _centered(frame, top + index * pitch, line, color, font)
    return frame


def calendar_cells(day_local: str) -> list[tuple[int, int, int]]:
    """(day, column, row) for every day of the month, Monday in column 0."""
    day = date.fromisoformat(day_local)
    first = date(day.year, day.month, 1).weekday()
    count = calendar.monthrange(day.year, day.month)[1]
    return [(n, (first + n - 1) % 7, (first + n - 1) // 7) for n in range(1, count + 1)]


def calendar_layout(day_local: str) -> tuple[int, dict[int, tuple[int, int]]]:
    """(y of the weekday letters, top-left pixel of each day's cell). The letters and the grid
    are centred as one block in the space under the month's name."""
    cells = calendar_cells(day_local)
    rows = cells[-1][2] + 1
    block = SMALL.height + HEADER_GAP + rows * CELL_PITCH - (CELL_PITCH - CELL)
    header_y = CALENDAR_TOP + (SIZE - CALENDAR_TOP - block) // 2
    grid_y = header_y + SMALL.height + HEADER_GAP
    return header_y, {
        n: (GRID_X + column * CELL_PITCH, grid_y + row * CELL_PITCH) for n, column, row in cells
    }


def month_color(month: int) -> Color:
    """A hue of its own for each month, so the calendar changes colour as the year turns."""
    return hue((month - 1) / 12 + 0.55)


def _calendar_frame(day_local: str) -> Frame:
    frame = new_frame()
    day = date.fromisoformat(day_local)
    color = month_color(day.month)
    name, year = MONTH_NAMES[day.month - 1], f"{day.year:04d}"
    if text_width(f"{name} {year}") > TEXT_WIDTH:
        name = name[:3]
    x = (SIZE - text_width(f"{name} {year}")) // 2
    after = draw_text(frame, x, TITLE_Y, name, legible(color))
    draw_text(frame, after + text_width(" ") + SMALL.spacing, TITLE_Y, year, TEXT)

    header_y, cells = calendar_layout(day_local)
    for column, letter in enumerate(WEEKDAY_LETTERS):
        x = GRID_X + column * CELL_PITCH + (CELL - text_width(letter)) // 2
        draw_text(frame, x, header_y, letter, LABEL)
    for n, (x, y) in cells.items():
        if n == day.day:
            shade = WHITE
        elif n < day.day:
            shade = dim(color, PAST_SHADE)
        else:
            shade = TRACK
        fill_rect(frame, x, y, x + CELL - 1, y + CELL - 1, shade)
    return frame


def render_month(view: DayView, now: datetime | None = None) -> Clip:
    """The month's feature for the requested day (the plate, then its note when it has one)
    and the calendar as the last page, as a paged clip. With no feature for that month, the
    calendar alone. `now` is unused; the screen is drawn from the day alone."""
    found = plate_for(view)
    if found is None:
        return still(_calendar_frame(view.day_local))
    feature, plate = found
    frames = [_plate_frame(feature, plate)]
    durations = [PLATE_MS]
    if plate.note.strip():
        frames.append(_note_frame(feature, plate))
        durations.append(NOTE_MS)
    frames.append(_calendar_frame(view.day_local))
    durations.append(CALENDAR_MS)
    return Clip(tuple(frames), tuple(durations))
