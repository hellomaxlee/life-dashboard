"""Two bitmap fonts drawn in code. No font files, no network.

BODY is the classic public-domain 5x7 LCD face (upper and lower case), used for the numbers
that matter and for the paged summary; at scale 2 it is the hero-number face. SMALL is a
3x5 capitals-only face for labels; lower case is drawn as capitals. Both have one pixel
between glyphs. A character neither font knows is drawn as '?', never skipped, so a missing
glyph is visible in the frame instead of silently changing a number.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.render.frame import Color, Frame

_BODY_COLUMNS: dict[str, tuple[int, int, int, int, int]] = {
    "!": (0x00, 0x00, 0x5F, 0x00, 0x00),
    '"': (0x00, 0x07, 0x00, 0x07, 0x00),
    "#": (0x14, 0x7F, 0x14, 0x7F, 0x14),
    "$": (0x24, 0x2A, 0x7F, 0x2A, 0x12),
    "%": (0x23, 0x13, 0x08, 0x64, 0x62),
    "&": (0x36, 0x49, 0x55, 0x22, 0x50),
    "'": (0x00, 0x05, 0x03, 0x00, 0x00),
    "(": (0x00, 0x1C, 0x22, 0x41, 0x00),
    ")": (0x00, 0x41, 0x22, 0x1C, 0x00),
    "*": (0x14, 0x08, 0x3E, 0x08, 0x14),
    "+": (0x08, 0x08, 0x3E, 0x08, 0x08),
    ",": (0x00, 0x50, 0x30, 0x00, 0x00),
    "-": (0x08, 0x08, 0x08, 0x08, 0x08),
    ".": (0x00, 0x60, 0x60, 0x00, 0x00),
    "/": (0x20, 0x10, 0x08, 0x04, 0x02),
    "0": (0x3E, 0x51, 0x49, 0x45, 0x3E),
    "1": (0x00, 0x42, 0x7F, 0x40, 0x00),
    "2": (0x42, 0x61, 0x51, 0x49, 0x46),
    "3": (0x21, 0x41, 0x45, 0x4B, 0x31),
    "4": (0x18, 0x14, 0x12, 0x7F, 0x10),
    "5": (0x27, 0x45, 0x45, 0x45, 0x39),
    "6": (0x3C, 0x4A, 0x49, 0x49, 0x30),
    "7": (0x01, 0x71, 0x09, 0x05, 0x03),
    "8": (0x36, 0x49, 0x49, 0x49, 0x36),
    "9": (0x06, 0x49, 0x49, 0x29, 0x1E),
    ":": (0x00, 0x36, 0x36, 0x00, 0x00),
    ";": (0x00, 0x56, 0x36, 0x00, 0x00),
    "<": (0x08, 0x14, 0x22, 0x41, 0x00),
    "=": (0x14, 0x14, 0x14, 0x14, 0x14),
    ">": (0x00, 0x41, 0x22, 0x14, 0x08),
    "?": (0x02, 0x01, 0x51, 0x09, 0x06),
    "@": (0x32, 0x49, 0x79, 0x41, 0x3E),
    "A": (0x7E, 0x11, 0x11, 0x11, 0x7E),
    "B": (0x7F, 0x49, 0x49, 0x49, 0x36),
    "C": (0x3E, 0x41, 0x41, 0x41, 0x22),
    "D": (0x7F, 0x41, 0x41, 0x22, 0x1C),
    "E": (0x7F, 0x49, 0x49, 0x49, 0x41),
    "F": (0x7F, 0x09, 0x09, 0x09, 0x01),
    "G": (0x3E, 0x41, 0x49, 0x49, 0x7A),
    "H": (0x7F, 0x08, 0x08, 0x08, 0x7F),
    "I": (0x00, 0x41, 0x7F, 0x41, 0x00),
    "J": (0x20, 0x40, 0x41, 0x3F, 0x01),
    "K": (0x7F, 0x08, 0x14, 0x22, 0x41),
    "L": (0x7F, 0x40, 0x40, 0x40, 0x40),
    "M": (0x7F, 0x02, 0x0C, 0x02, 0x7F),
    "N": (0x7F, 0x04, 0x08, 0x10, 0x7F),
    "O": (0x3E, 0x41, 0x41, 0x41, 0x3E),
    "P": (0x7F, 0x09, 0x09, 0x09, 0x06),
    "Q": (0x3E, 0x41, 0x51, 0x21, 0x5E),
    "R": (0x7F, 0x09, 0x19, 0x29, 0x46),
    "S": (0x46, 0x49, 0x49, 0x49, 0x31),
    "T": (0x01, 0x01, 0x7F, 0x01, 0x01),
    "U": (0x3F, 0x40, 0x40, 0x40, 0x3F),
    "V": (0x1F, 0x20, 0x40, 0x20, 0x1F),
    "W": (0x3F, 0x40, 0x38, 0x40, 0x3F),
    "X": (0x63, 0x14, 0x08, 0x14, 0x63),
    "Y": (0x07, 0x08, 0x70, 0x08, 0x07),
    "Z": (0x61, 0x51, 0x49, 0x45, 0x43),
    "[": (0x00, 0x7F, 0x41, 0x41, 0x00),
    "\\": (0x02, 0x04, 0x08, 0x10, 0x20),
    "]": (0x00, 0x41, 0x41, 0x7F, 0x00),
    "^": (0x04, 0x02, 0x01, 0x02, 0x04),
    "_": (0x40, 0x40, 0x40, 0x40, 0x40),
    "`": (0x00, 0x01, 0x02, 0x04, 0x00),
    "a": (0x20, 0x54, 0x54, 0x54, 0x78),
    "b": (0x7F, 0x48, 0x44, 0x44, 0x38),
    "c": (0x38, 0x44, 0x44, 0x44, 0x20),
    "d": (0x38, 0x44, 0x44, 0x48, 0x7F),
    "e": (0x38, 0x54, 0x54, 0x54, 0x18),
    "f": (0x08, 0x7E, 0x09, 0x01, 0x02),
    "g": (0x0C, 0x52, 0x52, 0x52, 0x3E),
    "h": (0x7F, 0x08, 0x04, 0x04, 0x78),
    "i": (0x00, 0x44, 0x7D, 0x40, 0x00),
    "j": (0x20, 0x40, 0x44, 0x3D, 0x00),
    "k": (0x7F, 0x10, 0x28, 0x44, 0x00),
    "l": (0x00, 0x41, 0x7F, 0x40, 0x00),
    "m": (0x7C, 0x04, 0x18, 0x04, 0x78),
    "n": (0x7C, 0x08, 0x04, 0x04, 0x78),
    "o": (0x38, 0x44, 0x44, 0x44, 0x38),
    "p": (0x7C, 0x14, 0x14, 0x14, 0x08),
    "q": (0x08, 0x14, 0x14, 0x18, 0x7C),
    "r": (0x7C, 0x08, 0x04, 0x04, 0x08),
    "s": (0x48, 0x54, 0x54, 0x54, 0x20),
    "t": (0x04, 0x3F, 0x44, 0x40, 0x20),
    "u": (0x3C, 0x40, 0x40, 0x20, 0x7C),
    "v": (0x1C, 0x20, 0x40, 0x20, 0x1C),
    "w": (0x3C, 0x40, 0x30, 0x40, 0x3C),
    "x": (0x44, 0x28, 0x10, 0x28, 0x44),
    "y": (0x0C, 0x50, 0x50, 0x50, 0x3C),
    "z": (0x44, 0x64, 0x54, 0x4C, 0x44),
    "{": (0x00, 0x08, 0x36, 0x41, 0x00),
    "|": (0x00, 0x00, 0x7F, 0x00, 0x00),
    "}": (0x00, 0x41, 0x36, 0x08, 0x00),
    "~": (0x08, 0x04, 0x08, 0x10, 0x08),
}
_BODY_NARROW = ".,:;!'|il"

_SMALL_ROWS: dict[str, str] = {
    "A": ".#. #.# ### #.# #.#",
    "B": "##. #.# ##. #.# ##.",
    "C": ".## #.. #.. #.. .##",
    "D": "##. #.# #.# #.# ##.",
    "E": "### #.. ##. #.. ###",
    "F": "### #.. ##. #.. #..",
    "G": ".## #.. #.# #.# .##",
    "H": "#.# #.# ### #.# #.#",
    "I": "### .#. .#. .#. ###",
    "J": "..# ..# ..# #.# .#.",
    "K": "#.# #.# ##. #.# #.#",
    "L": "#.. #.. #.. #.. ###",
    "M": "#...# ##.## #.#.# #...# #...#",
    "N": "#..# ##.# #.## #..# #..#",
    "O": ".#. #.# #.# #.# .#.",
    "P": "##. #.# ##. #.. #..",
    "Q": ".#. #.# #.# ### .##",
    "R": "##. #.# ##. #.# #.#",
    "S": ".## #.. .#. ..# ##.",
    "T": "### .#. .#. .#. .#.",
    "U": "#.# #.# #.# #.# ###",
    "V": "#.# #.# #.# #.# .#.",
    "W": "#...# #...# #.#.# #.#.# .#.#.",
    "X": "#.# #.# .#. #.# #.#",
    "Y": "#.# #.# .#. .#. .#.",
    "Z": "### ..# .#. #.. ###",
    "0": "### #.# #.# #.# ###",
    "1": ".#. ##. .#. .#. ###",
    "2": "### ..# ### #.. ###",
    "3": "### ..# .## ..# ###",
    "4": "#.# #.# ### ..# ..#",
    "5": "### #.. ### ..# ###",
    "6": "### #.. ### #.# ###",
    "7": "### ..# ..# .#. .#.",
    "8": "### #.# ### #.# ###",
    "9": "### #.# ### ..# ###",
    " ": ".. .. .. .. ..",
    ":": ". # . # .",
    ".": ". . . . #",
    ",": ".. .. .. .# #.",
    "'": "# # . . .",
    "!": "# # # . #",
    "-": "... ... ### ... ...",
    "+": "... .#. ### .#. ...",
    "/": "..# ..# .#. #.. #..",
    "%": "#.. ..# .#. #.. ..#",
    "<": "..# .#. #.. .#. ..#",
    ">": "#.. .#. ..# .#. #..",
    "?": "### ..# .#. ... .#.",
}

_REPLACEMENTS = {
    "‘": "'",
    "’": "'",
    "“": '"',
    "”": '"',
    "–": "-",
    "—": "-",
    "…": "...",
    " ": " ",
}


@dataclass(frozen=True)
class Font:
    name: str
    height: int
    glyphs: dict[str, tuple[str, ...]]
    capitals_only: bool = False
    spacing: int = 1

    def glyph(self, char: str) -> tuple[str, ...]:
        if self.capitals_only:
            char = char.upper()
        return self.glyphs.get(char) or self.glyphs["?"]


def _body_glyphs() -> dict[str, tuple[str, ...]]:
    glyphs: dict[str, tuple[str, ...]] = {" ": tuple("..." for _ in range(7))}
    for char, columns in _BODY_COLUMNS.items():
        used = list(columns)
        if char in _BODY_NARROW:
            first = next(i for i, c in enumerate(columns) if c)
            last = max(i for i, c in enumerate(columns) if c)
            used = list(columns[first : last + 1])
        glyphs[char] = tuple(
            "".join("#" if column >> row & 1 else "." for column in used) for row in range(7)
        )
    return glyphs


BODY = Font("body-5x7", 7, _body_glyphs())
SMALL = Font(
    "small-3x5",
    5,
    {char: tuple(rows.split()) for char, rows in _SMALL_ROWS.items()},
    capitals_only=True,
)


def normalize(text: str) -> str:
    """Fold typographic punctuation to the ASCII the fonts carry."""
    for src, dst in _REPLACEMENTS.items():
        text = text.replace(src, dst)
    return text


def text_width(text: str, font: Font = SMALL, scale: int = 1) -> int:
    text = normalize(text)
    if not text:
        return 0
    cells = sum(len(font.glyph(ch)[0]) for ch in text) + font.spacing * (len(text) - 1)
    return cells * scale


def draw_text(
    frame: Frame, x: int, y: int, text: str, color: Color, font: Font = SMALL, scale: int = 1
) -> int:
    """Draw text with its top-left at (x, y), clipped to the frame. Returns the x after it."""
    pixels = frame.load()
    width, height = frame.size
    cursor = x
    for char in normalize(text):
        rows = font.glyph(char)
        glyph_width = len(rows[0]) * scale
        if cursor + glyph_width > 0 and cursor < width:
            for row_index, row in enumerate(rows):
                for col_index, cell in enumerate(row):
                    if cell != "#":
                        continue
                    for dy in range(scale):
                        py = y + row_index * scale + dy
                        if not 0 <= py < height:
                            continue
                        for dx in range(scale):
                            px = cursor + col_index * scale + dx
                            if 0 <= px < width:
                                pixels[px, py] = color
        cursor += glyph_width + font.spacing * scale
    return cursor


def draw_text_right(
    frame: Frame, right: int, y: int, text: str, color: Color, font: Font = SMALL, scale: int = 1
) -> int:
    """Draw text so its last pixel column is `right`. Returns the x it started at."""
    x = right - text_width(text, font, scale) + 1
    draw_text(frame, x, y, text, color, font, scale)
    return x


def draw_text_centered(
    frame: Frame, y: int, text: str, color: Color, font: Font = SMALL, scale: int = 1
) -> int:
    x = (frame.width - text_width(text, font, scale)) // 2
    draw_text(frame, x, y, text, color, font, scale)
    return x
