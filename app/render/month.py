"""The Month screen: the month's feature for the day, then the month as a calendar.

With a feature (app/month/spec.py) for the day's month the screen is two pages: the feature,
always one page (Max, 2026-10-09: "the monthly feature should be contained to one slide as a
general rule"; the note page that followed it is gone), the day's 16x16 plate hung on the
weekday's ground: with a fact (every feature since 2026-11-15) the plate at two LEDs a cell
on a mat, the caption and the fact on up to three lines; without one, the title over the plate
at three LEDs a cell, the caption, and a rail of one pip per day down each side; then the
calendar, every day of every month (Max, 2026-10-07: "I really like this panel").

The calendar is the month's name and year and a Monday-first grid with one cell per day: today
bright, the days to come recessed, and each day gone drawn in what it earned when the view
carries that month's stored wins (`DayView.month_wins`; soft cells in the month's colour when
it does not). The wins are read from the metrics rows as written; nothing is computed here.
Without a feature (none generated yet, the model unavailable or over
budget) it is the whole screen.

The day is always the view's requested day, never `day_shown`: the Today screen may fall back
to yesterday's facts, but the month does not turn back with it.
"""

from __future__ import annotations

import calendar
from datetime import date, datetime

from app.month.spec import ART_SIZE, FACT_LINES, UNLIT, DayPlate, MonthFeature, fact_lines
from app.render.font import SMALL, Font, draw_text, text_width
from app.render.frame import SIZE, Clip, Color, Frame, new_frame, still
from app.render.palette import (
    BLACK,
    GOLD,
    LABEL,
    TEAL,
    TEXT,
    TRACK,
    VIOLET,
    WHITE,
    day_ground,
    dim,
    hue,
    legible_on,
)
from app.render.screens import fill_rect, page_ground, slot
from app.render.theme import STOCK, MonthTheme, theme_for
from app.render.view import DayView

PLATE_MS = 6000
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
PAST_SHADE = 0.7


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


def draw_plate(
    frame: Frame,
    plate: DayPlate,
    palette: tuple[Color, ...],
    scale: int = PLATE_SCALE,
    left: int = PLATE_X,
    top: int = PLATE_Y,
) -> None:
    """Each art cell as a `scale` block of its palette colour; an unlit cell stays black."""
    for row, cells in enumerate(plate.art[:ART_SIZE]):
        for column, cell in enumerate(cells[:ART_SIZE]):
            if cell == UNLIT or not cell.isdigit() or not 1 <= int(cell) <= len(palette):
                continue
            x, y = left + column * scale, top + row * scale
            fill_rect(frame, x, y, x + scale - 1, y + scale - 1, palette[int(cell) - 1])


def rail_pips(days: int) -> list[tuple[int, int, int]]:
    """(day, x, y) of each day's pip: the left rail top to bottom, then the right."""
    return [
        (day, RAIL_XS[(day - 1) // RAIL_ROWS], PLATE_Y + (day - 1) % RAIL_ROWS * RAIL_PITCH)
        for day in range(1, min(days, 2 * RAIL_ROWS) + 1)
    ]


def _draw_rails(
    frame: Frame, today: int, days: int, palette: tuple[Color, ...], track: Color = TRACK
) -> None:
    for day, x, y in rail_pips(days):
        if day == today:
            color = WHITE
        elif day < today:
            color = dim(palette[1 % len(palette)], RAIL_PAST)
        else:
            color = track
        fill_rect(frame, x, y, x + RAIL_PIP - 1, y + RAIL_PIP - 1, color)


FACT_SCALE = 2
FACT_PLATE_Y = 2
FACT_CAPTION_Y = 37
FACT_YS = (44, 50, 56)
assert len(FACT_YS) == FACT_LINES, "one row for each line a fact may take"


def _plate_frame(feature: MonthFeature, plate: DayPlate, day_local: str | None = None) -> Frame:
    """The day's plate. With a fact that fits (`spec.fact_lines`): the plate at twice its
    cells on a mat a shade deeper than the wall, the caption, then the fact on up to three
    lines; the feature's title gives its row to the fact (the caption names the day's
    subject). Without one (a feature
    stored before facts were asked for): the plate at three times with the day rails and the
    caption at the foot, as it always was. Either way it hangs on the weekday's wall."""
    ground = day_ground(day_local) if day_local is not None else BLACK
    frame = new_frame(ground)
    palette = feature.palette
    mat = slot(ground, BLACK)
    title = legible_on(legible(palette[0]), ground)
    caption = legible_on(legible(palette[1 % len(palette)]), ground)
    fact = fact_lines(plate.fact) if plate.fact else None
    if fact is None:
        _centered(frame, TITLE_Y, feature.title, title)
        fill_rect(frame, PLATE_X - 1, PLATE_Y - 1, PLATE_X + PLATE_PX, PLATE_Y + PLATE_PX, mat)
        draw_plate(frame, plate, palette)
        _draw_rails(frame, plate.day, len(feature.days), palette, slot(ground, TRACK))
        _centered(frame, CAPTION_Y, plate.caption, caption)
        return frame
    side = ART_SIZE * FACT_SCALE
    x0 = (SIZE - side) // 2
    fill_rect(frame, x0 - 1, FACT_PLATE_Y - 1, x0 + side, FACT_PLATE_Y + side, mat)
    draw_plate(frame, plate, palette, FACT_SCALE, x0, FACT_PLATE_Y)
    _centered(frame, FACT_CAPTION_Y, plate.caption, caption)
    for line, y in zip(fact, FACT_YS[: len(fact)], strict=True):
        _centered(frame, y, line, TEXT)
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


QUIET_DAY: Color = (96, 100, 128)
WIN_COLORS: dict[str, Color] = {"workout": GOLD, "sleep": VIOLET, "book": TEAL}


def _draw_wins(frame: Frame, x: int, y: int, wins: tuple[str, ...], quiet: Color) -> None:
    """A day gone, always a whole cell so the grid holds (display audit, 2026-11-15): gold
    for a workout, violet for sleep in its band, gold over violet for both, `quiet` for a day
    that earned neither; a finished book is a teal square set in the cell's lower right
    corner. The wins' colours are the sparkle icons'."""
    half = CELL // 2
    top = WIN_COLORS["workout"] if "workout" in wins else None
    bottom = WIN_COLORS["sleep"] if "sleep" in wins else None
    fill_rect(frame, x, y, x + CELL - 1, y + half - 1, top or bottom or quiet)
    fill_rect(frame, x, y + half, x + CELL - 1, y + CELL - 1, bottom or top or quiet)
    if "book" in wins:
        fill_rect(frame, x + CELL - 3, y + CELL - 3, x + CELL - 1, y + CELL - 1, WIN_COLORS["book"])


def _calendar_frame(
    day_local: str,
    theme: MonthTheme = STOCK,
    month_wins: tuple[tuple[int, tuple[str, ...]], ...] = (),
    ground: Color = BLACK,
) -> Frame:
    """With a month theme (a stored feature), the month's name and the days gone take its
    accent and the days to come its tinted track (Max, 2026-11-09: "calibrate the calendar
    boxes screen with the month's theme color"); without one, the month's own hue and the
    stock track. The days gone are at PAST_SHADE: at 0.5 an accent and its track sat too close
    at 1x and the palette's lighter hues went to mud, and at full strength a pale accent hides
    today's white. A day gone that earned a win is drawn in the win's colours instead
    (`_draw_wins`, style B, 2026-11-15) and, once any day's wins are known, a day with
    none is a whole cell of one quiet grey: shown plainly, and never mistaken for a win when
    the month's accent sits near gold or violet. With no wins known the days gone are whole
    cells of the past shade, as before."""
    frame = new_frame(ground)
    day = date.fromisoformat(day_local)
    color = theme.accent or month_color(day.month)
    name, year = MONTH_NAMES[day.month - 1], f"{day.year:04d}"
    if text_width(f"{name} {year}") > TEXT_WIDTH:
        name = name[:3]
    x = (SIZE - text_width(f"{name} {year}")) // 2
    after = draw_text(frame, x, TITLE_Y, name, legible_on(legible(color), ground))
    draw_text(frame, after + text_width(" ") + SMALL.spacing, TITLE_Y, year, TEXT)

    header_y, cells = calendar_layout(day_local)
    for column, letter in enumerate(WEEKDAY_LETTERS):
        x = GRID_X + column * CELL_PITCH + (CELL - text_width(letter)) // 2
        draw_text(frame, x, header_y, letter, legible_on(LABEL, ground))
    known = dict(month_wins)
    for n, (x, y) in cells.items():
        if n < day.day and month_wins:
            _draw_wins(frame, x, y, known.get(n, ()), QUIET_DAY)
            continue
        if n == day.day:
            shade = WHITE
        elif n < day.day:
            shade = dim(color, PAST_SHADE)
        else:
            shade = slot(ground, theme.track)
        fill_rect(frame, x, y, x + CELL - 1, y + CELL - 1, shade)
    return frame


def render_month(view: DayView, now: datetime | None = None) -> Clip:
    """The month's feature for the requested day, one page whatever the stored plate carries
    (Max, 2026-10-09), and the calendar as the last page, as a paged clip. With no feature
    for that month, the calendar alone. `now` is unused; the screen is drawn from the day
    alone."""
    found = plate_for(view)
    ground = page_ground(view)
    if found is None:
        return still(_calendar_frame(view.day_local, month_wins=view.month_wins, ground=ground))
    feature, plate = found
    plate_page = _plate_frame(feature, plate, view.day_local)
    calendar_page = _calendar_frame(view.day_local, theme_for(view), view.month_wins, ground)
    return Clip((plate_page, calendar_page), (PLATE_MS, CALENDAR_MS))
