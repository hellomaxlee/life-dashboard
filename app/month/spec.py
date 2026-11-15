"""The month feature: one theme a month, one small plate a day, threaded by the theme.

The model authors a MonthFeature once a month (app/month/generate.py); the renderer draws the
day's plate (app/render/month.py). This module is the contract between them and the only
judge of what may reach the panel: `parse_feature` accepts a decoded JSON object or raises
SpecError naming the first thing wrong, and nothing unparsed is ever stored or drawn.

Shape, as JSON:
  {"month": "2026-10", "title": "...", "theme": "...", "palette": ["#rrggbb", ...],
   "days": [{"day": 1, "caption": "...", "art": ["16 chars", ... 16 rows]}, ...]}

- title: 1 to TITLE_MAX characters, drawn in capitals above the plate.
- theme: the month's idea in a sentence or two, up to THEME_MAX characters; web page only.
- palette: PALETTE_MIN to PALETTE_MAX colours. An art cell is "." (unlit, black) or the digit
  of a palette colour, "1" being the first. Every colour must be bright enough for LEDs.
- days: exactly one entry per day of that month, in order.
  - caption: 1 to CAPTION_MAX characters, drawn in capitals under the plate.
  - fact: one true, specific thing about the day's subject, up to FACT_MAX characters, that
    fits FACT_LINES lines of the small face in capitals (Max, 2026-11-15: the feature
    "should be educational in some way"). Asked for and required when a feature is authored
    (`parse_feature(..., facts=True)`), drawn under the caption on the same one page.
  - note: optional, up to NOTE_MAX characters. Features stored before 2026-10-09 carry one;
    it is checked like any text but never drawn or asked for.
  - art: ART_SIZE rows of ART_SIZE cells, at least ART_MIN_LIT of them lit.
Text may use only characters the panel's faces can draw, and no digits: the feature is given
none of Max's data, so a number in it could only be invented.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass

from app.render.font import BODY, SMALL, normalize, text_width
from app.render.frame import Color

TITLE_MAX = 15
THEME_MAX = 300
CAPTION_MAX = 15
NOTE_MAX = 60
FACT_MAX = 44
FACT_LINES = 3
FACT_WIDTH = 60
ART_SIZE = 16
ART_MIN_LIT = 12
PALETTE_MIN = 2
PALETTE_MAX = 8
MIN_BRIGHT_CHANNEL = 140
UNLIT = "."
MONTH = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


class SpecError(ValueError):
    pass


@dataclass(frozen=True)
class DayPlate:
    day: int
    caption: str
    note: str
    art: tuple[str, ...]
    fact: str = ""


@dataclass(frozen=True)
class MonthFeature:
    month: str
    title: str
    theme: str
    palette: tuple[Color, ...]
    days: tuple[DayPlate, ...]

    def plate(self, day_local: str) -> DayPlate | None:
        """The plate for a YYYY-MM-DD day, or None if the day is not in this month."""
        if day_local[:7] != self.month:
            return None
        return self.days[int(day_local[8:10]) - 1]


def days_in(month: str) -> int:
    return calendar.monthrange(int(month[:4]), int(month[5:7]))[1]


def _text(where: str, value: object, low: int, high: int, face: str) -> str:
    if not isinstance(value, str):
        raise SpecError(f"{where}: not a string")
    text = " ".join(normalize(value).split())
    if not low <= len(text) <= high:
        raise SpecError(f"{where}: {len(text)} characters, wants {low} to {high}")
    if any(ch.isdigit() for ch in text):
        raise SpecError(f"{where}: digits are not allowed")
    glyphs = SMALL.glyphs if face == "small" else BODY.glyphs
    shown = text.upper() if face == "small" else text
    missing = sorted({ch for ch in shown if ch not in glyphs})
    if missing:
        raise SpecError(f"{where}: the panel cannot draw {''.join(missing)!r}")
    return text


def fact_lines(text: str) -> list[str] | None:
    """A fact as the panel draws it: capitals in the small face, wrapped on words to
    FACT_WIDTH pixels. None when it is empty, needs more than FACT_LINES lines, holds a word
    wider than a line, or uses a character the small face cannot draw."""
    shown = " ".join(normalize(text).upper().split())
    if not shown or any(ch not in SMALL.glyphs for ch in shown):
        return None
    lines: list[str] = []
    for word in shown.split():
        candidate = f"{lines[-1]} {word}" if lines else word
        if lines and text_width(candidate, SMALL) <= FACT_WIDTH:
            lines[-1] = candidate
        elif text_width(word, SMALL) <= FACT_WIDTH:
            lines.append(word)
        else:
            return None
    return lines if len(lines) <= FACT_LINES else None


def _fact(where: str, entry: dict, required: bool) -> str:
    """The day's fact: required when a feature is authored, optional when one is read back
    (a feature stored before 2026-11-15 has none)."""
    if not required and "fact" not in entry:
        return ""
    return _text(f"{where} fact", entry.get("fact"), 1, FACT_MAX, "small")


def _color(where: str, value: object) -> Color:
    if not isinstance(value, str) or not HEX.match(value):
        raise SpecError(f"{where}: not a #rrggbb colour")
    color = (int(value[1:3], 16), int(value[3:5], 16), int(value[5:7], 16))
    if max(color) < MIN_BRIGHT_CHANNEL:
        raise SpecError(f"{where}: {value} is too dark for LEDs")
    return color


def _art(where: str, value: object, colours: int) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) != ART_SIZE:
        raise SpecError(f"{where}: wants {ART_SIZE} rows")
    allowed = UNLIT + "".join(str(n) for n in range(1, colours + 1))
    for number, row in enumerate(value, start=1):
        if not isinstance(row, str) or len(row) != ART_SIZE:
            raise SpecError(f"{where} row {number}: wants {ART_SIZE} cells")
        if any(cell not in allowed for cell in row):
            raise SpecError(f"{where} row {number}: a cell is not one of {allowed!r}")
    if sum(cell != UNLIT for row in value for cell in row) < ART_MIN_LIT:
        raise SpecError(f"{where}: fewer than {ART_MIN_LIT} lit cells")
    return tuple(value)


def parse_feature(raw: object, month: str, facts: bool = False) -> MonthFeature:
    """Validate a decoded JSON object as the feature for `month` (YYYY-MM). With `facts`,
    every day must carry a fact that fits the panel: the rule for a feature being authored."""
    if not MONTH.match(month):
        raise SpecError(f"month {month!r} is not YYYY-MM")
    if not isinstance(raw, dict):
        raise SpecError("the feature is not a JSON object")
    if raw.get("month") != month:
        raise SpecError(f"month: got {raw.get('month')!r}, wants {month!r}")
    title = _text("title", raw.get("title"), 1, TITLE_MAX, "small")
    theme = raw.get("theme")
    if not isinstance(theme, str) or not 1 <= len(theme.strip()) <= THEME_MAX:
        raise SpecError(f"theme: wants 1 to {THEME_MAX} characters")
    colours = raw.get("palette")
    if not isinstance(colours, list) or not PALETTE_MIN <= len(colours) <= PALETTE_MAX:
        raise SpecError(f"palette: wants {PALETTE_MIN} to {PALETTE_MAX} colours")
    palette = tuple(_color(f"palette {n}", c) for n, c in enumerate(colours, start=1))
    entries = raw.get("days")
    if not isinstance(entries, list) or len(entries) != days_in(month):
        raise SpecError(f"days: wants {days_in(month)} entries")
    plates = []
    for number, entry in enumerate(entries, start=1):
        where = f"day {number}"
        if not isinstance(entry, dict) or entry.get("day") != number:
            raise SpecError(f"{where}: missing or out of order")
        plates.append(
            DayPlate(
                number,
                _text(f"{where} caption", entry.get("caption"), 1, CAPTION_MAX, "small"),
                _text(f"{where} note", entry.get("note", ""), 0, NOTE_MAX, "body"),
                _art(f"{where} art", entry.get("art"), len(palette)),
                _fact(where, entry, facts),
            )
        )
    if facts:
        long = [plate.day for plate in plates if fact_lines(plate.fact) is None]
        if long:
            listed = ", ".join(str(day) for day in long)
            raise SpecError(
                f"fact: the facts of day(s) {listed} do not fit {FACT_LINES} lines of about "
                "eleven capitals broken between words; shorten each of them"
            )
    if len({plate.art for plate in plates}) < len(plates) // 2:
        raise SpecError("days: more than half the plates repeat another day's art")
    return MonthFeature(month, title, theme.strip(), palette, tuple(plates))
