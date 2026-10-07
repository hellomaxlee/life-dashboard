"""The three rotation screens: Week, Today, Books + summary.

Each takes a DayView and returns a Clip. Every None in the view is drawn as a stated
fallback, so a view with nothing in it still yields three full frames. Rest is drawn plainly
and never as a miss: an empty dot is a quiet ring, a short night is a blue number, not a red one.

The summary is shown as word-wrapped pages, two lines at a time, about two seconds each. A
pixel scroll of a 110-character line needs some 300 frames and the device holds fewer than
60, so pages are the form that reaches the panel as drawn. Its three voices are told apart by
colour: a quotation in VOICE, its "- Author" in ATTRIBUTION, Max's own words in TEXT.

Each screen's header word wears its own accent (palette.HEADERS) so the rotation reads as
distinct places; the stamp on the right and the field labels inside keep TEXT and LABEL.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.render.font import (
    BODY,
    SMALL,
    draw_text,
    draw_text_centered,
    draw_text_right,
    normalize,
    text_width,
)
from app.render.frame import SIZE, Clip, Color, Frame, new_frame, still
from app.render.palette import (
    ATTRIBUTION,
    DOTS,
    GOLD,
    GREEN,
    HEADERS,
    LABEL,
    RING,
    SECONDARY,
    SKY,
    SPINES,
    TEXT,
    TRACK,
    VIOLET,
    VOICE,
    WHITE,
    WOOD,
    dim,
    shelf_tint,
)
from app.render.usage import draw_usage, usage_state
from app.render.view import DayView, has_health_data, valid_count, valid_sleep_hours
from app.timeutil import from_utc_iso

LEFT = 2
RIGHT = 61
DOT_LABEL_X = 13
DOT_ROW_Y = 17
STREAK_Y = 28
SLEEP_BAR_HOURS = 10
LINE_WIDTH = 60
WORD_WIDTH = SIZE - LEFT
PAGE_MS = 2000
TITLE_GAP = 3
SUMMARY_MAX_CHARS = 220
SUMMARY_LINE_YS = (45, 54)
PAGE_PIP_Y = SIZE - 1
NO_SUMMARY = "No summary yet."
_WEEKDAYS = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")
_SPINE_HEIGHTS = (13, 11, 14, 12, 13, 10, 14, 12, 11, 13, 12, 14)
MOON = (
    "...####..",
    "..###....",
    ".###.....",
    "###......",
    "###......",
    "###......",
    "####....#",
    ".####..##",
    "..######.",
    "...####..",
)


def _in_disc(dx: int, dy: int, radius: int) -> bool:
    return dx * dx + dy * dy <= radius * radius + radius


def draw_disc(frame: Frame, cx: int, cy: int, radius: int, color: Color) -> None:
    pixels = frame.load()
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            x, y = cx + dx, cy + dy
            if _in_disc(dx, dy, radius) and 0 <= x < SIZE and 0 <= y < SIZE:
                pixels[x, y] = color


def draw_ring(
    frame: Frame, cx: int, cy: int, radius: int, color: Color, dashed: bool = False
) -> None:
    pixels = frame.load()
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            x, y = cx + dx, cy + dy
            on_edge = _in_disc(dx, dy, radius) and not _in_disc(dx, dy, radius - 1)
            if not on_edge or not (0 <= x < SIZE and 0 <= y < SIZE):
                continue
            if dashed and (dx + dy) % 3 == 0:
                continue
            pixels[x, y] = color


def draw_bitmap(frame: Frame, x: int, y: int, rows: tuple[str, ...], color: Color) -> None:
    pixels = frame.load()
    for row_index, row in enumerate(rows):
        for col_index, cell in enumerate(row):
            px, py = x + col_index, y + row_index
            if cell == "#" and 0 <= px < SIZE and 0 <= py < SIZE:
                pixels[px, py] = color


def fill_rect(frame: Frame, x0: int, y0: int, x1: int, y1: int, color: Color) -> None:
    """Inclusive rectangle, clipped to the frame."""
    pixels = frame.load()
    for y in range(max(y0, 0), min(y1, SIZE - 1) + 1):
        for x in range(max(x0, 0), min(x1, SIZE - 1) + 1):
            pixels[x, y] = color


def dot_layout(target: int) -> tuple[list[int], int]:
    """Centre x of each weekly dot and their shared radius."""
    count = max(target, 1)
    radius = max(2, min(7, (SIZE // count - 4) // 2))
    centres = [int(SIZE * (2 * i + 1) / (2 * count) + 0.5) for i in range(count)]
    return centres, radius


def dot_color(index: int) -> Color:
    return DOTS[index % len(DOTS)]


def _count(value: int | None) -> int | None:
    return value if valid_count(value) else None


def _draw_streak(frame: Frame, streak: int) -> None:
    """ "N WK STREAK", centred. Steps down in size until the whole line fits the frame."""
    number = str(streak)
    for font, tail in ((BODY, "WK STREAK"), (BODY, "WK"), (SMALL, "WK")):
        total = text_width(number, font) + 3 + text_width(tail, SMALL)
        if total <= RIGHT - LEFT + 1:
            x = (SIZE - total) // 2
            y = STREAK_Y if font is BODY else STREAK_Y + 2
            after = draw_text(frame, x, y, number, GOLD if streak else TEXT, font)
            draw_text(frame, after + 2, STREAK_Y + 2, tail, LABEL, SMALL)
            return
    draw_text_centered(frame, STREAK_Y + 2, "MANY WK STREAK", LABEL, SMALL)


def _week_frame(view: DayView, now: datetime) -> Frame:
    frame = new_frame()
    draw_text(frame, LEFT, 2, "WEEK", HEADERS["week"], SMALL)
    centres, radius = dot_layout(view.week_target)
    dots = _count(view.week_dots)
    streak = _count(view.streak_weeks)
    if dots is None:
        draw_text_right(frame, RIGHT, 2, "NO DATA", TEXT, SMALL)
    else:
        draw_text_right(frame, RIGHT, 2, f"{dots} OF {view.week_target}", TEXT, SMALL)
    for index, cx in enumerate(centres):
        if dots is None:
            draw_ring(frame, cx, DOT_ROW_Y, radius, RING, dashed=True)
        elif index < dots:
            draw_disc(frame, cx, DOT_ROW_Y, radius, dot_color(index))
        else:
            draw_ring(frame, cx, DOT_ROW_Y, radius, RING)

    if streak is None:
        draw_text_centered(frame, STREAK_Y + 2, "STREAK NO DATA", TEXT, SMALL)
    else:
        _draw_streak(frame, streak)

    draw_usage(frame, usage_state(view.claude, now, view.stale_hours))
    return frame


def render_week(view: DayView, now: datetime) -> Clip:
    """Three dots, the weeks-hit streak under them, the Claude usage bar at the bottom.
    A still even when the usage reading is stale: amber dot, amber age line."""
    return still(_week_frame(view, now))


def sleep_text(hours: float) -> str:
    """One decimal, truncated, so the frame never shows 7.0 for a night short of 7."""
    return f"{int(hours * 10 + 1e-6) / 10:.1f}"


def sleep_met(view: DayView) -> bool:
    """The sleep win as the frame shows it: the displayed number against the target."""
    if not valid_sleep_hours(view.sleep_hours):
        return False
    return float(sleep_text(view.sleep_hours)) >= view.sleep_target_hours


def sleep_fill(hours: float) -> int:
    """Bar pixels for a night, floored, so a night short of the goal never touches its tick."""
    return min(60, int(hours * 60 / SLEEP_BAR_HOURS + 1e-9))


def _hours_label(hours: float) -> str:
    return f"{int(hours)}H" if hours == int(hours) else f"{hours:.1f}H"


def _has_day_data(view: DayView) -> bool:
    return (
        valid_sleep_hours(view.sleep_hours)
        or _count(view.steps) is not None
        or view.today_dot is not None
    )


def as_of_label(view: DayView, now: datetime | None = None) -> str | None:
    """When the day's data is from, or None to leave the line out.

    Same day: the clock. One to six days back: weekday and clock, which is unambiguous within
    a week. Older: whole days ("AS OF 8D AGO"), so an old push never reads as recent. A push
    dated after the view's day, or after `now`, cannot have fed this day and counts as none.
    With no usable push the line says "NO PUSH YET" only when the day has no data either; a day
    whose numbers a later push back-filled gets no as-of line rather than a false one.
    """
    missing = None if _has_day_data(view) else "NO PUSH YET"
    if view.as_of_utc is None:
        return missing
    moment = from_utc_iso(view.as_of_utc)
    if now is not None and moment > now:
        return missing
    local = moment.astimezone(ZoneInfo(view.home_tz))
    days_back = (date.fromisoformat(view.day_local) - local.date()).days
    if days_back < 0:
        return missing
    clock = local.strftime("%H:%M")
    if days_back == 0:
        return f"AS OF {clock}"
    if days_back <= 6:
        return f"AS OF {_WEEKDAYS[local.weekday()]} {clock}"
    return f"AS OF {days_back}D AGO"


def no_dot_label(view: DayView) -> str:
    """Why the day has no dot, from the day's own row: no workout on a day a push has
    covered, a workout with no usable heart rate, or its load against the week's bar
    (floored, so it never reads as met). "NO DOT YET" when the row does not say."""
    if view.workout_count == 0 and has_health_data(view):
        return "NO WORKOUT"
    if not view.workout_count:
        return "NO DOT YET"
    if view.workout_load is None:
        return "NO HR DATA"
    if view.load_bar is not None and 0 <= view.workout_load < view.load_bar:
        label = f"LOAD {int(view.workout_load)}/{int(view.load_bar)}"
        if DOT_LABEL_X + text_width(label, SMALL) - 1 <= RIGHT:
            return label
    return "NO DOT YET"


def render_today(view: DayView, now: datetime | None = None) -> Clip:
    """Last night's sleep against the target, today's dot, steps, and when the data is from."""
    frame = new_frame()
    day = date.fromisoformat(view.day_shown or view.day_local)
    title = "TODAY" if view.day_shown in (None, view.day_local) else "YESTERDAY"
    draw_text(frame, LEFT, 2, title, HEADERS["today"], SMALL)
    weekday = _WEEKDAYS[day.weekday()]
    stamp = f"{weekday} {day.day}"
    if LEFT + text_width(title, SMALL) + TITLE_GAP > RIGHT + 1 - text_width(stamp, SMALL):
        stamp = weekday
    draw_text_right(frame, RIGHT, 2, stamp, TEXT, SMALL)

    draw_bitmap(frame, LEFT, 11, MOON, VIOLET)
    target_x = LEFT + int(view.sleep_target_hours / SLEEP_BAR_HOURS * 60)
    fill_rect(frame, LEFT, 26, RIGHT, 28, TRACK)
    if not valid_sleep_hours(view.sleep_hours):
        draw_text(frame, 16, 9, "--", TEXT, BODY, scale=2)
        draw_text(frame, LEFT, 32, "SLEEP", LABEL, SMALL)
        draw_text_right(frame, RIGHT, 32, "NO DATA", TEXT, SMALL)
    else:
        color = GREEN if sleep_met(view) else SKY
        number = sleep_text(view.sleep_hours)
        unit_room = 3 + text_width("h", BODY)
        x = min(16, RIGHT + 1 - text_width(number, BODY, 2) - unit_room)
        after = draw_text(frame, x, 9, number, color, BODY, scale=2)
        draw_text(frame, after + 1, 16, "h", color, BODY)
        filled = sleep_fill(view.sleep_hours)
        if filled:
            fill_rect(frame, LEFT, 26, LEFT + filled - 1, 28, color)
        draw_text(frame, LEFT, 32, "SLEEP", LABEL, SMALL)
        draw_text_right(
            frame, RIGHT, 32, f"GOAL {_hours_label(view.sleep_target_hours)}", TEXT, SMALL
        )
    fill_rect(frame, target_x, 25, target_x, 29, WHITE)

    if view.today_dot is None:
        draw_ring(frame, 6, 44, 4, RING, dashed=True)
        draw_text(frame, 13, 42, "DOT NO DATA", TEXT, SMALL)
    elif view.today_dot:
        draw_disc(frame, 6, 44, 4, GOLD)
        draw_text(frame, 13, 42, "WORKOUT DONE", GOLD, SMALL)
    else:
        draw_ring(frame, 6, 44, 4, RING)
        draw_text(frame, DOT_LABEL_X, 42, no_dot_label(view), TEXT, SMALL)

    draw_text(frame, LEFT, 51, "STEPS", LABEL, SMALL)
    steps = "NO DATA" if _count(view.steps) is None else str(view.steps)
    draw_text_right(frame, RIGHT, 51, steps, TEXT, SMALL)
    as_of = as_of_label(view, now)
    if as_of is not None:
        draw_text(frame, LEFT, 58, as_of, SECONDARY, SMALL)
    return still(frame)


def _draw_book_count(frame: Frame, count: int, target: int) -> None:
    """The big count and "of N". Steps down in size until the whole line fits the frame."""
    number, tail = str(count), f"of {target}"
    room = RIGHT - LEFT + 1
    big = text_width(number, BODY, 2)
    if big + 4 + text_width(tail, BODY) <= room:
        after = draw_text(frame, LEFT, 9, number, GOLD, BODY, scale=2)
        draw_text(frame, after + 2, 16, tail, TEXT, BODY)
    elif big + 2 + text_width(tail, SMALL) <= room:
        after = draw_text(frame, LEFT, 9, number, GOLD, BODY, scale=2)
        draw_text(frame, after, 18, tail, TEXT, SMALL)
    elif text_width(number, BODY) + 2 + text_width(tail, SMALL) <= room:
        after = draw_text(frame, LEFT, 16, number, GOLD, BODY)
        draw_text(frame, after + 1, 18, tail, TEXT, SMALL)
    else:
        draw_text(frame, LEFT, 16, "MANY", GOLD, BODY)


def _books_base(view: DayView) -> Frame:
    frame = new_frame()
    draw_text(frame, LEFT, 2, "BOOKS", HEADERS["books"], SMALL)
    draw_text_right(frame, RIGHT, 2, view.day_local[:4], TEXT, SMALL)
    read = _count(view.books_ytd)
    if read is None:
        draw_text(frame, LEFT, 9, "--", TEXT, BODY, scale=2)
        draw_text_right(frame, RIGHT, 16, "NO DATA", TEXT, SMALL)
    else:
        _draw_book_count(frame, read, view.books_target)

    slots = max(view.books_target, 1)
    spine = max(1, (60 - (slots - 1)) // slots)
    for index in range(slots):
        x0 = LEFT + index * (spine + 1)
        if x0 + spine - 1 > RIGHT:
            break
        top = 41 - _SPINE_HEIGHTS[index % len(_SPINE_HEIGHTS)]
        color = SPINES[index % len(SPINES)]
        if index < (read or 0):
            fill_rect(frame, x0, top, x0 + spine - 1, 40, color)
            fill_rect(frame, x0, top + 2, x0 + spine - 1, top + 2, dim(color, 0.45))
        else:
            fill_rect(frame, x0, top, x0 + spine - 1, 40, shelf_tint(color))
    fill_rect(frame, LEFT, 41, RIGHT, 42, WOOD)
    return frame


_MARKDOWN_PAIR = re.compile(r"(?<!\w)(\*\*|__|\*|_)(?=\S)(.+?)(?<=\S)\1(?!\w)")
_UNITS = frozenset(
    {"h", "hr", "hrs", "min", "mins", "km", "mi", "m", "bpm", "wk", "wks", "d", "kg", "lb", "%"}
)
_TRAILING = ".,;:!?)"
_GLUE = "\u00a0"


def clean_summary(text: str) -> str:
    """Fold the summary to what the 5x7 face can draw.

    Typographic quotes and dashes become ASCII, accents are folded (NFKD), paired markdown
    emphasis markers (*x*, _x_, **x**, __x__) are removed, and any character the font has no
    glyph for, emoji included, is dropped rather than drawn as "?". Whitespace is collapsed.
    """
    text = normalize(text)
    text = normalize(unicodedata.normalize("NFKD", text))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = _MARKDOWN_PAIR.sub(r"\2", text)
    text = "".join(ch if ch in BODY.glyphs else " " if ch.isspace() else "" for ch in text)
    return " ".join(text.split())


def fit_summary(text: str) -> str:
    """Clean the text; past SUMMARY_MAX_CHARS, cut at a word and end with a visible "..."."""
    text = clean_summary(text)
    if len(text) <= SUMMARY_MAX_CHARS:
        return text
    room = SUMMARY_MAX_CHARS - 3
    kept = text[:room]
    if text[room] != " " and " " in kept:
        kept = kept[: kept.rindex(" ")]
    return kept.rstrip(" .,;:") + "..."


def _is_number(word: str) -> bool:
    return word.lstrip("(")[:1].isdigit()


def _glue_numbers(words: list[str]) -> list[str]:
    """Join a number to its unit ("9.8 h") or to "of N" ("(10 of 12);") so they wrap as one."""
    groups: list[str] = []
    index = 0
    while index < len(words):
        take = 1
        if _is_number(words[index]):
            following = words[index + 1 : index + 3]
            if len(following) == 2 and following[0].lower() == "of" and _is_number(following[1]):
                take = 3
            elif following and following[0].rstrip(_TRAILING).lower() in _UNITS:
                take = 2
        group = words[index : index + take]
        if text_width(" ".join(group), BODY) <= LINE_WIDTH:
            groups.append(_GLUE.join(group))
        else:
            groups.extend(group)
        index += take
    return groups


def _glue_attribution(words: list[str]) -> list[str]:
    """Keep the dash after a quotation with the author's first name when they fit. A dash in
    the middle of a sentence is left alone."""
    units: list[str] = []
    index = 0
    while index < len(words):
        word = words[index]
        after_quote = index > 0 and words[index - 1].rstrip(".,;:").endswith('"')
        if word == "-" and after_quote and index + 1 < len(words):
            joined = f"- {words[index + 1]}"
            if text_width(joined, BODY) <= WORD_WIDTH:
                units.append(joined.replace(" ", _GLUE))
                index += 2
                continue
        units.append(word)
        index += 1
    return units


def _at_hyphens(units: list[str]) -> list[str]:
    """A hyphenated word too wide for a line breaks after its hyphens ("water-" / "drops"),
    never in the middle of a part."""
    out: list[str] = []
    for unit in units:
        if "-" in unit.strip("-") and text_width(unit.replace(_GLUE, " "), BODY) > WORD_WIDTH:
            parts = unit.replace("-", "-\n").split("\n")
            out.extend(part for part in parts if part)
        else:
            out.append(unit)
    return out


def wrap_lines(text: str) -> list[str]:
    """Greedy word wrap to LINE_WIDTH pixels.

    A number stays on the same line as its unit or its "of N" whenever the group fits a
    line, and an attribution dash stays with its name. A word alone on a line may run into
    the right margin (WORD_WIDTH), so "Yesterday's" is not cut before its "s"; a wider
    hyphenated word breaks after a hyphen, and only a wider unhyphenated word is ever split.
    """
    lines: list[str] = []
    current = ""
    for unit in _at_hyphens(_glue_attribution(_glue_numbers(text.split()))):
        word = unit.replace(_GLUE, " ")
        candidate = f"{current} {word}" if current else word
        if text_width(candidate, BODY) <= LINE_WIDTH:
            current = candidate
            continue
        if current:
            lines.append(current)
        bare = word.rstrip(_TRAILING)
        if bare and text_width(bare, BODY) <= WORD_WIDTH < text_width(word, BODY):
            word = bare
        while text_width(word, BODY) > WORD_WIDTH:
            cut = len(word) - 1
            while cut > 1 and text_width(word[:cut], BODY) > LINE_WIDTH:
                cut -= 1
            lines.append(word[:cut])
            word = word[cut:]
        current = word
    if current:
        lines.append(current)
    return lines


def wrap_pages(text: str) -> list[tuple[str, ...]]:
    """The summary as pages of at most two lines, truncated first if it is over length."""
    lines = wrap_lines(fit_summary(text))
    per_page = len(SUMMARY_LINE_YS)
    return [tuple(lines[i : i + per_page]) for i in range(0, len(lines), per_page)]


_CLAUSE_END = ".,;:!?"


def summary_colors(text: str) -> list[Color]:
    """One colour per character of a wrapped summary (its lines joined by spaces).

    Max's rule (2026-10-05): the quotation in white, the author's name in gold, his own
    clause in a third colour. So when the text holds a double-quoted quotation, the
    quotation, marks included, is TEXT; a "- Author" after a closing mark is ATTRIBUTION to
    the end of its clause (the first of .,;:!? inclusive) or of the text; everything else,
    the data clause or the reflection around the quote, is VOICE. A text with no quotation,
    or an odd number of marks, is all TEXT as before. A space takes the colour of the
    character before it, so runs stay whole across the words they join.
    """
    colors = [TEXT] * len(text)
    if text.count('"') % 2 or '"' not in text:
        return colors
    colors = [VOICE] * len(text)
    index = 0
    while index < len(text):
        if text[index] != '"':
            index += 1
            continue
        close = text.index('"', index + 1)
        colors[index : close + 1] = [TEXT] * (close + 1 - index)
        index = close + 1
        dash = index
        while dash < len(text) and text[dash] in " .,;:":
            dash += 1
        if text[dash : dash + 2] != "- ":
            continue
        end = dash
        while end < len(text) and text[end] not in _CLAUSE_END:
            end += 1
        end = min(end + 1, len(text))
        colors[dash:end] = [ATTRIBUTION] * (end - dash)
        index = end
    for position in range(1, len(text)):
        if text[position] == " ":
            colors[position] = colors[position - 1]
    return colors


def line_colors(lines: list[str]) -> list[list[Color]]:
    """The colours of every character of every wrapped line, so a quotation that crosses a
    line or a page keeps its colour on each."""
    colors = summary_colors(" ".join(lines))
    out: list[list[Color]] = []
    start = 0
    for line in lines:
        out.append(colors[start : start + len(line)])
        start += len(line) + 1
    return out


def _draw_runs(frame: Frame, x: int, y: int, line: str, colors: list[Color]) -> None:
    """Draw a line as runs of one colour, each starting where the last left off, so the
    pixels are those of one draw of the whole line and a single-colour line is one call."""
    start = 0
    for end in range(1, len(line) + 1):
        if end == len(line) or colors[end] != colors[start]:
            x = draw_text(frame, x, y, line[start:end], colors[start], BODY)
            start = end


def _draw_page_pips(frame: Frame, page: int, pages: int) -> None:
    width = pages * 3 - 1
    x0 = (SIZE - width) // 2
    for index in range(pages):
        color = TEXT if index == page else RING
        fill_rect(frame, x0 + index * 3, PAGE_PIP_Y, x0 + index * 3 + 1, PAGE_PIP_Y, color)


def render_books(view: DayView) -> Clip:
    """Books this year on a shelf, and the day's one-line summary in pages underneath."""
    base = _books_base(view)
    pages = wrap_pages(view.summary_line or NO_SUMMARY) or wrap_pages(NO_SUMMARY)
    colors = iter(line_colors([line for page in pages for line in page]))
    frames: list[Frame] = []
    for number, page in enumerate(pages):
        frame = base.copy()
        for line, y in zip(page, SUMMARY_LINE_YS, strict=False):
            _draw_runs(frame, LEFT, y, line, next(colors))
        if len(pages) > 1:
            _draw_page_pips(frame, number, len(pages))
        frames.append(frame)
    if len(frames) == 1:
        return still(frames[0])
    return Clip(tuple(frames), (PAGE_MS,) * len(frames))
