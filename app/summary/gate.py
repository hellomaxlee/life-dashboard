"""The summary gate. A line passes only if every rule below passes; the first failing rule
names the reason. The same gate judges the model's line and the fallback's.

Rules, in order:
  length      the device line is 1 to 220 characters, no control characters
  grounding   every number token (digits, optional decimals, a bare ".5", thousands commas
              removed, digits glued to letters as in "x2"; "VO2" and "Z1".."Z5" are names)
              equals a number in the payload: "7.4" is 7.4, "118" is 118 or 118.0, "7" is 7.0.
              A line with no number passes. Numbers inside a verbatim quote-bank entry in
              double quotes ("a thousand miles") are the author's, not a claim about the day,
              and are not checked; numbers outside it are.
              Number words count like digits wherever they stand: zero, two..nineteen, the
              tens, hundred, thousand, million, dozen, half, twice, thrice, and compounds of
              them ("twenty-one", "a hundred"). "one" and the ordinal words (first, second,
              ...) count only next to a counted noun, so "one step at a time" and "at first"
              are not numbers; "a"/"an" never count. A digit ordinal ("1st") must sit next to
              a counted noun. Counted nouns: dot, workout, session, book, hour, week, minute,
              mile, day, night; "quality", "more" and the like may sit between, punctuation
              may not. A dot/workout/session/book/hour/week/minute count must equal a payload
              value of that kind ("third dot" is the week's dot count 3), and in "N of M
              dots" both are typed; a day/night/mile count any payload number. Vulgar
              fractions, superscripts and other non-ASCII numerals are rejected outright.
  ban         matched after NFKC folding (fullwidth and compatibility forms, curly
              apostrophes, zero-width characters). Stems (crush*, grind*, guilt*,
              disappoint*, hustl*, beast*, lazy/lazi*, shame*, slack*), streak-anxiety
              phrases with any determiner (don't break/let/lose the|your|this|a chain/streak,
              keep/protect/save the streak, break/broke the chain, streak die*, streak on the
              line/at stake/at risk), no [more] excuse(s), should/could have, you
              fail/failed/failing, you missed/skipped, stay/stayed hard, no day(s) off, you
              need/have/had/got to, you gotta, you better, you must, just do it, don't miss,
              try/work/push harder, do better, only/just N [noun] left/more/to go, any "!",
              any emoji or symbol character, a second exclamatory clause
  names       words go under a person's name only from the quote bank (app/summary/quotes.py).
              Curly quotes and dashes are folded first. A line with double quotation marks
              passes only if they are balanced, hold one quotation, its words match a bank
              entry (case, spacing and punctuation aside) and that entry's author is named
              outside the quotation with no other person beside it. A person named with no
              such quotation is rejected: a bank author with other words, an ancient or
              modern name from the lists, "as X said", "in the words of", "to quote", "who
              also said", a capitalised two-word name before says/said/would/wrote and the
              like, a closing "- Name". A bank entry written without its quotation marks is
              rejected. A tradition named in paraphrase ("the Stoics", "an old Buddhist
              idea") passes. The same quote or the same author within the last 14 lines is
              rejected.
  rest        no claim that he rested, on any day, weekends included: the payload has no
              positive rest signal, and no workout on record is absence of a record, not
              evidence of rest. Rejected: rest/recovery/easy/off day, day off, day of rest,
              you rested/recovered/took it easy/took the day off/chose rest, rested or rest
              or recovery next to yesterday/today/a weekday/the weekend, your rest, "no
              workout" (or training, session, run) unless followed by "on record",
              "recorded" or "logged", did not train, without a workout. Rest as an idea
              ("rest is where the training lands") passes.
  hard-days   in the week after a broken week only: no "streak", no "next week"
  wellness    HRV, resting heart rate, VO2 max, daylight only when the payload has a fact
  opening     the first three words do not open any of the last 14 lines
  similarity  character-trigram Jaccard against each of the last 30 lines is at most the
              configured threshold

A finished book's title is a stored value: it is replaced by a neutral word before the
grounding, ban, names, rest, hard-days and wellness checks, so "#1" in a series title is not an
invented number. Length is measured on the line as written.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from app.summary.payload import Payload
from app.summary.quotes import AUTHOR_ALIASES, QUOTES, Quote

DEVICE_MAX = 220
RECENT_OPENING_LINES = 14
RECENT_SIMILARITY_LINES = 30
SOURCE_WINDOW_LINES = 14

DETERMINER = r"(?:the|your|this|that|a|my|our)"
CHAIN = r"(?:chain|streak)"
COUNT_WORD = (
    r"(?:\d+|a\s+few|a\s+couple|another|one|two|three|four|five|six|seven|eight|nine|ten|"
    r"eleven|twelve)"
)

BANS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (label, re.compile(pattern))
    for label, pattern in (
        (
            "don't break the chain",
            r"\b(?:don't|dont|do not|never|can't|cant|cannot)\s+"
            rf"(?:break|let|lose|drop|end|ruin|snap|waste|blow)\s+{DETERMINER}\s+{CHAIN}\b",
        ),
        (
            "keep the streak",
            r"\b(?:keep(?:s|ing)?|kept|protect(?:s|ed|ing)?|sav(?:e|es|ed|ing)|"
            r"guard(?:s|ed|ing)?|defend(?:s|ed|ing)?|preserv(?:e|es|ed|ing)|"
            rf"maintain(?:s|ed|ing)?|extend(?:s|ed|ing)?)\s+{DETERMINER}\s+{CHAIN}\b",
        ),
        (
            "break the chain",
            rf"\b(?:break(?:s|ing)?|broke|broken)\s+{DETERMINER}\s+chain\b",
        ),
        ("streak die", r"\bstreak(?:'s)?\s+(?:is\s+|will\s+)?(?:die|dies|died|dying)\b"),
        (
            "streak on the line",
            rf"\b{CHAIN}(?:'s)?\s+(?:is\s+|was\s+)?"
            r"(?:on\s+the\s+line|at\s+stake|at\s+risk|in\s+danger|in\s+jeopardy)\b",
        ),
        ("crush", r"\bcrush\w*"),
        ("grind", r"\bgrind\w*"),
        ("guilt", r"\bguilt\w*"),
        ("disappoint", r"\bdisappoint\w*"),
        ("hustle", r"\bhustl\w*"),
        ("beast", r"\bbeast\w*"),
        ("lazy", r"\bla(?:zy|zi\w*)\b"),
        ("shame", r"\bshame\w*"),
        ("slacked", r"\bslack\w*"),
        ("no excuses", r"\bno\s+(?:more\s+)?excuses?\b"),
        ("should have", r"\b(?:should|could)(?:'ve|\s+have|\s+of|a)\b"),
        ("you failed", r"\byou(?:'ve|'re|\s+are|\s+have)?\s+(?:fail|failed|failing)\b"),
        (
            "you missed",
            r"\byou(?:'ve|\s+have|\s+had)?\s+(?:just\s+)?(?:miss|missed|skip|skipped)\b",
        ),
        ("stay hard", r"\bstay(?:s|ed|ing)?\s+hard\b"),
        ("no days off", r"\bno\s+days?\s+off\b"),
        (
            "you need to",
            r"\byou(?:'ve|'d)?\s+(?:(?:need|needed|have|had|got|ought)\s+to|gotta|better|"
            r"had\s+better)\b",
        ),
        ("you must", r"\byou\s+must\b"),
        ("just do it", r"\bjust\s+do\s+it\b"),
        ("don't miss", r"\b(?:don't|dont|do not)\s+miss\b"),
        (
            "try harder",
            r"\b(?:try|tries|tried|trying|work|works|working|push|pushes|pushing|train|"
            r"training|go|going|dig|digging)\s+harder\b",
        ),
        ("do better", r"\b(?:do|did|doing)\s+better\b"),
        (
            "only N left",
            rf"\b(?:only|just)\s+{COUNT_WORD}\s+(?:\w+\s+){{0,2}}?"
            r"(?:more|left|to\s+go|remaining|short)\b",
        ),
    )
)

OTHER_SOURCES = (
    "socrates",
    "plato",
    "zeno",
    "chrysippus",
    "cleanthes",
    "musonius rufus",
    "musonius",
    "cato",
    "cicero",
    "plutarch",
    "epicurus",
    "diogenes",
    "pythagoras",
    "the buddha",
    "buddha",
    "mencius",
    "sun tzu",
    "rumi",
    "pascal",
    "goethe",
    "kant",
    "nietzsche",
    "kierkegaard",
    "schopenhauer",
    "tolstoy",
    "shakespeare",
    "franklin",
    "lincoln",
    "twain",
    "einstein",
    "gandhi",
    "churchill",
    "camus",
    "frankl",
    "mandela",
    "thich nhat hanh",
    "alan watts",
    "bruce lee",
    "muhammad ali",
    "lombardi",
    "prefontaine",
)
MODERN_NAMES = (
    "goggins",
    "jocko",
    "willink",
    "huberman",
    "ryan holiday",
    "brene brown",
    "brené brown",
    "peterson",
    "musk",
    "tony robbins",
    "oprah",
    "ferriss",
    "james clear",
    "atomic habits",
    "kipchoge",
    "kobe",
    "steve jobs",
    "dalai lama",
    "pema chodron",
    "eckhart tolle",
    "kabat-zinn",
    "murakami",
    "michael jordan",
    "lebron",
    "serena",
)
SPEECH_VERBS = (
    r"(?:said|says|put\s+it|puts\s+it|wrote|writes|taught|teaches|noted|notes|observed|"
    r"observes|reminds\s+us|reminded\s+us|tells\s+us|told\s+us|would\s+say|liked\s+to\s+say)"
)
ATTRIBUTION = re.compile(
    rf"\bas\s+(?:[\w'-]+\s+){{1,4}}?(?:once\s+|often\s+)?{SPEECH_VERBS}\b"
    r"|\bin\s+the\s+words\s+of\b|\bto\s+quote\b|\bquoting\b"
    rf"|\b(?:who|he|she)\s+(?:also\s+|once\s+|often\s+)?{SPEECH_VERBS}\b",
    re.IGNORECASE,
)
CLOSING_ATTRIBUTION = re.compile(r"(?:^|\s)-{1,2}\s*(?:[A-Z][\w'.]*\s*){1,4}[.]?\s*$")
QUOTED = re.compile(r'"([^"]*)"')
QUOTE_MARKS = str.maketrans(
    {
        "“": '"',
        "”": '"',
        "„": '"',
        "‟": '"',
        "«": '"',
        "»": '"',
        "″": '"',
        "–": "-",
        "—": "-",
        "―": "-",
        "‒": "-",
        "‐": "-",
        "‑": "-",
        "−": "-",
    }
)
QUOTE_STAND_IN = "a quotation"
NAMED_SPEAKER = re.compile(
    r"\b((?:[A-Z][a-z'-]+\s+){1,3}[A-Z][a-z'-]+)\s+(?:once\s+|often\s+)?"
    r"(said|says|would|noted|notes|wrote|writes|taught|teaches|called|calls|believed|believes)\b"
)
NAME_OPENERS = frozenset({"the", "a", "an", "your", "my", "our", "this", "that", "and", "but"})
WELLNESS_WORDS = (
    "hrv",
    "heart rate variability",
    "resting heart rate",
    "resting hr",
    "vo2",
    "daylight",
)
HARD_DAY_WORDS = ("streak", "next week")
A_DAY = (
    r"(?:yesterday|today|monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    r"(?:the|this|last)\s+weekend|last\s+night)"
)
RESTING = r"(?:rest|resting|rested|recovery|recovering)"
REST_CLAIMS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (label, re.compile(pattern))
    for label, pattern in (
        ("rest day", r"\b(?:rest|resting|recovery|easy|off|down)\s+days?\b"),
        ("day off", r"\b(?:days?|weekends?)\s+(?:off|of\s+rest|of\s+recovery)\b"),
        (
            "you rested",
            r"\byou(?:'ve|\s+have|\s+had)?\s+(?:\w+\s+)?(?:rested|recovered|"
            r"took\s+(?:it\s+easy|a\s+break|a\s+rest|rest|the\s+pause)|"
            r"chose\s+(?:rest|recovery|the\s+pause|to\s+rest|not\s+to\s+train))\b",
        ),
        ("took it easy", r"\b(?:took|taking|take)\s+it\s+easy\b"),
        ("rested yesterday", rf"\b{RESTING}\s+(?:on\s+)?{A_DAY}\b"),
        (
            "yesterday was rest",
            rf"\b{A_DAY}(?:'s)?\s+(?:was|is|were|went\s+to)\s+(?:a\s+|for\s+|about\s+)?{RESTING}\b",
        ),
        ("yesterday's rest", rf"\b{A_DAY}'s\s+{RESTING}\b"),
        ("your rest", r"\byour\s+(?:rest|recovery)\b(?!\s+(?:is|was)\s+yours)"),
        (
            "no workout",
            r"\bno\s+(?:workouts?|training|sessions?|run|runs|exercise)\b"
            r"(?!\s+(?:on\s+(?:the\s+)?record|recorded|logged|was\s+recorded))",
        ),
        (
            "did not train",
            r"\b(?:did\s+not|didn't|didnt)\s+(?:train|work\s+out|run|exercise)\b",
        ),
        ("without a workout", r"\bwithout\s+(?:a\s+)?(?:workout|training|a\s+run)\b"),
    )
)
EXCLAMATORY_OPENERS = re.compile(r"^(?:what\s+an?\b|how\s+\w+\b|such\s+an?\b|so\s+\w+\s*$)")

THOUSANDS = re.compile(r"(?<=\d),(?=\d{3}\b)")
WORD = re.compile(r"[a-z0-9']+")
TOKEN = re.compile(r"[a-z][a-z0-9']*|\d+(?:\.\d+)?|\.\d+|[^\sa-z0-9'-]")
DIGITS = re.compile(r"\d+")
APOSTROPHES = str.maketrans({"’": "'", "‘": "'", "ʼ": "'", "′": "'", "`": "'"})
LETTER_DIGIT_NAMES = frozenset({"vo2", "z1", "z2", "z3", "z4", "z5"})
ORDINAL_SUFFIXES = frozenset({"st", "nd", "rd", "th"})

CARDINALS = (
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
    "fifteen sixteen seventeen eighteen nineteen"
).split()
ORDINAL_WORDS = (
    "zeroth first second third fourth fifth sixth seventh eighth ninth tenth eleventh twelfth "
    "thirteenth fourteenth fifteenth sixteenth seventeenth eighteenth nineteenth"
).split()
TENS = "twenty thirty forty fifty sixty seventy eighty ninety".split()
TENS_ORDINALS = (
    "twentieth thirtieth fortieth fiftieth sixtieth seventieth eightieth ninetieth".split()
)
UNIT_WORDS = {w: float(i) for i, w in enumerate(CARDINALS)} | {
    w: float(20 + 10 * i) for i, w in enumerate(TENS)
}
SCALE_WORDS = {"hundred": 100.0, "thousand": 1000.0, "million": 1_000_000.0, "dozen": 12.0}
ORDINAL_VALUES = (
    {w: float(i) for i, w in enumerate(ORDINAL_WORDS)}
    | {w: float(20 + 10 * i) for i, w in enumerate(TENS_ORDINALS)}
    | {"hundredth": 100.0, "thousandth": 1000.0}
)
LONE_WORDS = {"half": 0.5, "twice": 2.0, "thrice": 3.0}
UNCOUNTED = "uncounted"
NOUN_KINDS = {
    "dot": "dots",
    "dots": "dots",
    "workout": "workouts",
    "workouts": "workouts",
    "session": "workouts",
    "sessions": "workouts",
    "book": "books",
    "books": "books",
    "hour": "hours",
    "hours": "hours",
    "week": "weeks",
    "weeks": "weeks",
    "minute": "minutes",
    "minutes": "minutes",
    "day": None,
    "days": None,
    "night": None,
    "nights": None,
    "mile": None,
    "miles": None,
}
BETWEEN = frozenset({"quality", "more", "full", "straight", "whole", "hard", "good", "solid"})
TITLE_STAND_IN = "the book"


@dataclass(frozen=True)
class Recent:
    """What the gate remembers: the newest first in every list."""

    opening_lines: tuple[str, ...] = ()
    similarity_lines: tuple[str, ...] = ()
    sources_named: tuple[str, ...] = ()
    """Every person named in the last SOURCE_WINDOW_LINES lines, as `persons_in` keys."""


@dataclass(frozen=True)
class GateResult:
    ok: bool
    reasons: tuple[str, ...] = field(default_factory=tuple)

    @property
    def reason(self) -> str:
        return "; ".join(self.reasons) if self.reasons else "pass"


@dataclass(frozen=True)
class Claim:
    """One number the line states. `soft` is "one" or an ordinal word: a number only next to
    a counted noun. `digits` are grounded by `ungrounded_numbers` and typed here only next
    to a counted noun. `ordinal_digit` is "1st": a number that must sit next to one."""

    text: str
    value: float
    soft: bool = False
    digits: bool = False
    ordinal_digit: bool = False


def fold(text: str) -> str:
    """NFKC with one apostrophe and no zero-width characters: what the ban list reads."""
    folded = unicodedata.normalize("NFKC", text).translate(APOSTROPHES)
    return "".join(c for c in folded if unicodedata.category(c) != "Cf")


def odd_numerals(text: str) -> list[str]:
    """Vulgar fractions, superscripts, circled and roman numerals: never grounded."""
    return [c for c in text if unicodedata.category(c) in ("No", "Nl")]


def _tokens(text: str) -> list[str]:
    return TOKEN.findall(THOUSANDS.sub("", fold(text).lower()))


def _is_digits(token: str) -> bool:
    return token[0].isdigit() or (token[0] == "." and len(token) > 1)


def _glued_digits(token: str) -> list[str]:
    """The digits of a letter-led token such as "x2"; none for a name such as "vo2"."""
    if not token[0].isalpha() or token in LETTER_DIGIT_NAMES:
        return []
    return DIGITS.findall(token)


def number_tokens(text: str) -> list[str]:
    found: list[str] = []
    for token in _tokens(text):
        if _is_digits(token):
            found.append(token)
        else:
            found.extend(_glued_digits(token))
    return found


def ungrounded_numbers(text: str, allowed: frozenset[float]) -> list[str]:
    return [tok for tok in number_tokens(text) if float(tok) not in allowed]


def _get(data: dict, *path: str) -> object:
    for key in path:
        if not isinstance(data, dict):
            return None
        data = data.get(key)
    return data


def typed_numbers(payload: Payload, kind: str | None) -> frozenset[float]:
    """The payload values a count of `kind` may equal; any payload number when untyped."""
    if kind is None:
        return payload.numbers()
    if kind == UNCOUNTED:
        return frozenset()
    d = payload.data
    paths = {
        "dots": (
            ("week", "quality_workouts"),
            ("week", "previous_week_quality_workouts"),
            ("week", "target"),
        ),
        "workouts": (
            ("day", "workout_count"),
            ("week", "quality_workouts"),
            ("week", "previous_week_quality_workouts"),
            ("week", "target"),
        ),
        "books": (("books", "ytd"), ("books", "target")),
        "hours": (("day", "sleep_hours"), ("targets", "sleep_hours")),
        "weeks": (("week", "weeks_hit_streak"),),
        "minutes": (
            ("day", "workout_minutes"),
            ("wellness_fact", "value"),
            ("wellness_fact", "baseline"),
        ),
    }[kind]
    values = (_get(d, *path) for path in paths)
    return frozenset(
        float(v) for v in values if isinstance(v, int | float) and not isinstance(v, bool)
    )


def _word_run(tokens: list[str], start: int) -> tuple[Claim, int] | None:
    """The number-word compound starting at `start` ("twenty one", "two hundred and five",
    "twenty first") and the index after it."""
    total = current = 0.0
    last: str | None = None
    words: list[str] = []
    i = start
    while i < len(tokens):
        tok = tokens[i]
        if tok in UNIT_WORDS or tok in ORDINAL_VALUES and tok not in SCALE_WORDS:
            value = UNIT_WORDS.get(tok, ORDINAL_VALUES.get(tok, 0.0))
            fits = (
                last is None
                or last == "scale"
                or (last == "tens" and 0 < value < 10)
                or (last == "and" and value < 100)
            )
            if not fits:
                break
            if tok in ORDINAL_VALUES:
                if value >= 100:
                    current = max(current, 1.0) * value
                else:
                    current += value
                words.append(tok)
                i += 1
                return Claim(" ".join(words), total + current, soft=True), i
            current += value
            last = "tens" if value >= 20 else "unit"
        elif tok in SCALE_WORDS:
            if last == "and":
                break
            scale = SCALE_WORDS[tok]
            if scale >= 1000:
                total += max(current, 1.0) * scale
                current = 0.0
            else:
                current = max(current, 1.0) * scale
            last = "scale"
        elif (
            tok == "and" and last == "scale" and i + 1 < len(tokens) and tokens[i + 1] in UNIT_WORDS
        ):
            last = "and"
            i += 1
            continue
        else:
            break
        words.append(tok)
        i += 1
    if not words:
        return None
    return Claim(" ".join(words), total + current, soft=words == ["one"]), i


def _items(tokens: list[str]) -> list[Claim | str]:
    """Tokens with every number (digits, number words) folded into a Claim."""
    items: list[Claim | str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if _is_digits(tok):
            ordinal = i + 1 < len(tokens) and tokens[i + 1] in ORDINAL_SUFFIXES
            items.append(Claim(tok, float(tok), digits=True, ordinal_digit=ordinal))
            i += 2 if ordinal else 1
            continue
        if tok in LONE_WORDS:
            items.append(Claim(tok, LONE_WORDS[tok]))
            i += 1
            continue
        run = _word_run(tokens, i)
        if run is not None:
            items.append(run[0])
            i = run[1]
            continue
        items.append(tok)
        i += 1
    return items


def _kind_after(items: list[Claim | str], index: int) -> str | None:
    """The kind of the counted noun the number at `index` sits next to, or UNCOUNTED.
    In "two of three dots" the first number takes the second's noun."""
    j = index + 1
    while j < len(items) and j <= index + 2 and items[j] in BETWEEN:
        j += 1
    if j < len(items) and isinstance(items[j], str) and items[j] in NOUN_KINDS:
        return NOUN_KINDS[items[j]]
    if j + 1 < len(items) and items[j] == "of" and isinstance(items[j + 1], Claim):
        return _kind_after(items, j + 1)
    return UNCOUNTED


def counted_numbers(text: str) -> list[tuple[str, float, str | None]]:
    """(token, value, kind) for every number word, and for digits next to a counted noun.
    Kind is the counted noun's kind, None for any payload number, UNCOUNTED for a digit
    ordinal with no noun to count."""
    items = _items(_tokens(text))
    found: list[tuple[str, float, str | None]] = []
    for i, item in enumerate(items):
        if not isinstance(item, Claim):
            continue
        kind = _kind_after(items, i)
        if kind == UNCOUNTED:
            if item.soft or (item.digits and not item.ordinal_digit):
                continue
            kind = UNCOUNTED if item.ordinal_digit else None
        found.append((item.text, item.value, kind))
    return found


def _is_emoji(char: str) -> bool:
    code = ord(char)
    if code >= 0x1F000 or code == 0xFE0F or 0x2600 <= code <= 0x27BF:
        return True
    return unicodedata.category(char) in ("So", "Sk", "Cs")


def _clauses(text: str) -> list[str]:
    return [c.strip() for c in re.split(r"[.;:!?]+", text) if c.strip()]


def _exclamatory(clause: str) -> bool:
    words = clause.split()
    shouting = [w for w in words if len(w) >= 3 and w.isalpha() and w.isupper()]
    return bool(EXCLAMATORY_OPENERS.match(clause.lower())) or bool(shouting)


def normalize(text: str) -> str:
    return " ".join(WORD.findall(fold(text).lower()))


def opening_words(text: str, count: int = 3) -> tuple[str, ...]:
    return tuple(normalize(text).split()[:count])


def trigrams(text: str) -> set[str]:
    padded = f"  {normalize(text)}  "
    return {padded[i : i + 3] for i in range(len(padded) - 2)}


def similarity(a: str, b: str) -> float:
    ta, tb = trigrams(a), trigrams(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def fold_quotes(text: str) -> str:
    """`fold` with every double quotation mark straight and every dash a hyphen."""
    return fold(text).translate(QUOTE_MARKS)


def quote_key(text: str) -> str:
    """What makes two quotations the same words: case, spacing and punctuation aside."""
    return normalize(fold_quotes(text))


BANK: dict[str, Quote] = {quote_key(q.text): q for q in QUOTES}
PERSON_ALIASES: tuple[tuple[str, str], ...] = tuple(
    sorted(
        [(normalize(a), author.lower()) for author, names in AUTHOR_ALIASES.items() for a in names]
        + [(normalize(name), name) for name in OTHER_SOURCES],
        key=lambda pair: -len(pair[0]),
    )
)
BANK_AUTHORS = frozenset(author.lower() for author in AUTHOR_ALIASES)


def bank_entry(quoted: str) -> Quote | None:
    return BANK.get(quote_key(quoted))


def quotations_in(text: str) -> list[str]:
    return QUOTED.findall(fold_quotes(text))


def persons_in(text: str) -> list[str]:
    """The persons named in `text`, longest alias first, each once: a bank author as the
    lowercased bank name ("marcus aurelius"), any other listed source as listed."""
    padded = f" {normalize(fold_quotes(text))} "
    found: list[str] = []
    for alias, person in PERSON_ALIASES:
        if f" {alias} " in padded:
            if person not in found:
                found.append(person)
            padded = padded.replace(f" {alias} ", "  ")
    return found


def sources_in(text: str) -> list[str]:
    """Who a stored line named, for the memory: persons outside any quotation."""
    return persons_in(QUOTED.sub(" ", fold_quotes(text)))


def without_bank_quotes(text: str) -> str:
    """The line with each verbatim bank quotation replaced by a neutral word."""

    def stand_in(match: re.Match[str]) -> str:
        return f'"{QUOTE_STAND_IN}"' if bank_entry(match.group(1)) else match.group(0)

    return QUOTED.sub(stand_in, fold_quotes(text))


def _without_names(text: str, person: str) -> str:
    for alias, owner in PERSON_ALIASES:
        if owner == person:
            pattern = r"[\s-]+".join(re.escape(word) for word in alias.split())
            text = re.sub(rf"\b{pattern}\b", " ", text, flags=re.IGNORECASE)
    return text


def without_title(line: str, payload: Payload) -> str:
    title = payload.book_title
    return line.replace(title, TITLE_STAND_IN) if title else line


def check_length(line: str) -> list[str]:
    if not line.strip():
        return ["length: empty line"]
    if len(line) > DEVICE_MAX:
        return [f"length: {len(line)} chars, limit {DEVICE_MAX}"]
    if any(unicodedata.category(c)[0] == "C" for c in line):
        return ["length: control character"]
    return []


def check_grounding(line: str, payload: Payload) -> list[str]:
    text = without_bank_quotes(without_title(line, payload))
    bad = odd_numerals(text) + ungrounded_numbers(text, payload.numbers())
    for token, value, kind in counted_numbers(text):
        if value not in typed_numbers(payload, kind) and token not in bad:
            bad.append(token)
    return [f"grounding: {', '.join(bad)} not in payload"] if bad else []


def check_ban(line: str) -> list[str]:
    lowered = normalize(line)
    for label, pattern in BANS:
        if pattern.search(lowered):
            return [f"ban: '{label}'"]
    if "!" in fold(line):
        return ["ban: exclamation mark"]
    emoji = [c for c in line if _is_emoji(c)]
    if emoji:
        return [f"ban: emoji {emoji[0]!r}"]
    exclamatory = [c for c in _clauses(line) if _exclamatory(c)]
    if len(exclamatory) >= 2:
        return ["ban: second exclamatory clause"]
    return []


def check_names(
    line: str, recent_sources: tuple[str, ...], recent_lines: tuple[str, ...] = ()
) -> list[str]:
    text = fold_quotes(line)
    if text.count('"') % 2:
        return ["names: unbalanced quotation marks"]
    spans = QUOTED.findall(text)
    residue = QUOTED.sub(" ", text)
    if len(spans) > 1:
        return ["names: more than one quotation in one line"]
    entry = bank_entry(spans[0]) if spans else None
    if spans and entry is None:
        return [
            "names: the words in quotation marks are not a quote-bank entry, whole and "
            "unchanged; quote only from the bank, or write an original line with no "
            "quotation marks"
        ]
    named = persons_in(residue)
    if entry is not None:
        author = entry.author.lower()
        if author not in named:
            wrong = f", not {named[0]!r}" if named else ""
            return [
                f"names: that quotation is {entry.author}'s in the bank{wrong}; name its author"
            ]
        others = [person for person in named if person != author]
        if others:
            return [f"names: {others[0]!r} named beside a quotation by {entry.author}"]
        residue = _without_names(residue, author)
    elif named:
        return [
            f"names: {named[0]!r} named without a verbatim quote-bank entry in double "
            "quotation marks; quote from the bank or name no person"
        ]
    lowered = normalize(residue)
    padded = f" {lowered} "
    for key, quote in BANK.items():
        if f" {key} " in padded:
            return [
                f"names: a bank quotation by {quote.author} without its quotation marks and author"
            ]
    if ATTRIBUTION.search(residue):
        return ["names: attributed quote"]
    for name in MODERN_NAMES:
        if re.search(rf"\b{re.escape(normalize(name))}\b", lowered):
            return [f"names: living or modern person {name!r}"]
    for speaker in NAMED_SPEAKER.finditer(residue):
        words = normalize(speaker.group(1)).split()
        while words and words[0] in NAME_OPENERS:
            words = words[1:]
        if len(words) >= 2:
            return [f"names: named person {' '.join(words)!r}"]
    if CLOSING_ATTRIBUTION.search(residue):
        return ["names: a closing attribution to a person who is not a bank author"]
    if entry is not None:
        key = quote_key(entry.text)
        for previous in recent_lines[:SOURCE_WINDOW_LINES]:
            if any(quote_key(span) == key for span in quotations_in(previous)):
                return [f"names: this quotation appeared in the last {SOURCE_WINDOW_LINES} lines"]
        if entry.author.lower() in recent_sources:
            return [
                f"names: {entry.author} was already quoted in the last {SOURCE_WINDOW_LINES} lines"
            ]
    return []


def has_rest_signal(payload: Payload) -> bool:
    """Whether the payload positively marks the described day as rest. Nothing does yet:
    `workout_count: 0` and `quality_workout: false` say no workout reached the dashboard,
    and the day-type label is derived from that same absence."""
    return False


def check_rest_claim(line: str, payload: Payload) -> list[str]:
    if has_rest_signal(payload):
        return []
    lowered = normalize(line)
    for label, pattern in REST_CLAIMS:
        if pattern.search(lowered):
            return [
                f"rest: '{label}' claims he rested or did not train; no workout on record "
                "is not evidence of rest"
            ]
    return []


def check_hard_day(line: str, payload: Payload) -> list[str]:
    if payload.cell.streak_state != "broken-last-week":
        return []
    lowered = normalize(line)
    for word in HARD_DAY_WORDS:
        if re.search(rf"\b{re.escape(word)}\b", lowered):
            return [f"hard-days: {word!r} after a broken week"]
    return []


def check_wellness(line: str, payload: Payload) -> list[str]:
    if "wellness_fact" in payload.data:
        return []
    lowered = normalize(line)
    for word in WELLNESS_WORDS:
        if re.search(rf"\b{re.escape(normalize(word))}\b", lowered):
            return [f"wellness: {word!r} with no band crossing today"]
    return []


def check_opening(line: str, recent: Recent) -> list[str]:
    head = opening_words(line)
    if len(head) < 3:
        return []
    for previous in recent.opening_lines[:RECENT_OPENING_LINES]:
        if opening_words(previous) == head:
            return [f"opening: {' '.join(head)!r} opened a recent line"]
    return []


def check_similarity(line: str, recent: Recent, threshold: float) -> list[str]:
    for previous in recent.similarity_lines[:RECENT_SIMILARITY_LINES]:
        score = similarity(line, previous)
        if score > threshold:
            return [f"similarity: {score:.2f} to {previous!r} exceeds {threshold}"]
    return []


def _content_checks(line: str, payload: Payload, recent: Recent) -> list[str]:
    text = without_title(line, payload)
    return (
        check_grounding(line, payload)
        or check_ban(text)
        or check_names(text, recent.sources_named, recent.opening_lines)
        or check_rest_claim(text, payload)
        or check_hard_day(text, payload)
        or check_wellness(text, payload)
    )


def check_device_line(line: str, payload: Payload, recent: Recent, threshold: float) -> GateResult:
    reasons = (
        check_length(line)
        or _content_checks(line, payload, recent)
        or check_opening(line, recent)
        or check_similarity(line, recent, threshold)
    )
    return GateResult(not reasons, tuple(reasons))


def check_web_line(line: str, payload: Payload, recent: Recent) -> GateResult:
    reasons = _content_checks(line, payload, recent)
    return GateResult(not reasons, tuple(reasons))
