"""The summary gate. A line passes only if every rule below passes; the first failing rule
names the reason. The same gate judges the model's line and the fallback's.

Rules, in order:
  length      the device line is 1 to 110 characters, no control characters
  grounding   every number token (digits, optional decimals, thousands commas removed) equals
              a number in the payload: "7.4" is 7.4, "118" is 118 or 118.0, "7" is 7.0.
              Number words count: a cardinal or ordinal one..twenty / first..twentieth
              ("a"/"an" never count) next to a counted noun (dot, workout, book, hour, week,
              session, mile, day, night; "quality", "more" and the like may sit between) is
              a number. A dot/workout/session/book/hour/week count must equal a payload value
              of that kind ("third dot" is the week's dot count 3); a day/night/mile count any
              payload number. Digits next to a counted noun are typed the same way.
  ban         stems (crush*, grind*, guilt*, disappoint*, hustl*, beast*, lazy/lazi*, shame*,
              slack*), streak-anxiety phrases (don't break/let the chain/streak, keep the
              chain/streak, break/broke the chain, streak die*), no excuse(s), should
              have/should've, you fail/failed/failing, stay/stayed hard, no day(s) off, you
              need/have/had/got to, you must, just do it, don't miss, only N left/more/to go,
              any "!", any emoji or symbol character, a second exclamatory clause
  names       an attributed quote ("as [the] X said/says/wrote/taught/put it"), a modern
              name, or an ancient source named when one was already named in the last 7 days
  hard-days   in the week after a broken week only: no "streak", no "next week"
  wellness    HRV, resting heart rate, VO2 max, daylight only when the payload has a fact
  opening     the first three words do not open any of the last 14 lines
  similarity  character-trigram Jaccard against each of the last 30 lines is at most the
              configured threshold

A finished book's title is a stored value: it is replaced by a neutral word before the
grounding, ban, names, hard-days and wellness checks, so "#1" in a series title is not an
invented number. Length is measured on the line as written.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from app.summary.payload import Payload

DEVICE_MAX = 110
RECENT_OPENING_LINES = 14
RECENT_SIMILARITY_LINES = 30
SOURCE_WINDOW_DAYS = 7

BANS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (label, re.compile(pattern))
    for label, pattern in (
        (
            "don't break the chain",
            r"\b(?:don't|dont|do not|never)\s+(?:break|let)\s+the\s+(?:chain|streak)\b",
        ),
        ("keep the streak", r"\bkeep(?:s|ing)?\s+the\s+(?:chain|streak)\b"),
        ("break the chain", r"\b(?:break(?:s|ing)?|broke|broken)\s+the\s+chain\b"),
        ("streak die", r"\bstreak\s+(?:die|dies|died|dying)\b"),
        ("crush", r"\bcrush\w*"),
        ("grind", r"\bgrind\w*"),
        ("guilt", r"\bguilt\w*"),
        ("disappoint", r"\bdisappoint\w*"),
        ("hustle", r"\bhustl\w*"),
        ("beast", r"\bbeast\w*"),
        ("lazy", r"\bla(?:zy|zi\w*)\b"),
        ("shame", r"\bshame\w*"),
        ("slacked", r"\bslack\w*"),
        ("no excuses", r"\bno\s+excuses?\b"),
        ("should have", r"\bshould(?:'ve|\s+have)\b"),
        ("you failed", r"\byou(?:'ve|'re|\s+are|\s+have)?\s+(?:fail|failed|failing)\b"),
        ("stay hard", r"\bstay(?:s|ed|ing)?\s+hard\b"),
        ("no days off", r"\bno\s+days?\s+off\b"),
        ("you need to", r"\byou\s+(?:need|needed|have|had|got)\s+to\b"),
        ("you must", r"\byou\s+must\b"),
        ("just do it", r"\bjust\s+do\s+it\b"),
        ("don't miss", r"\b(?:don't|dont|do not)\s+miss\b"),
        (
            "only N left",
            r"\bonly\s+(?:\d+|one|two|three|four|a few|another)\s+(?:more|left|to go)\b",
        ),
    )
)

ANCIENT_SOURCES = (
    "marcus aurelius",
    "aurelius",
    "epictetus",
    "seneca",
    "aristotle",
    "socrates",
    "plato",
    "zeno",
    "chrysippus",
    "musonius",
    "cato",
    "buddha",
    "the buddha",
    "lao tzu",
    "laozi",
    "confucius",
    "heraclitus",
    "diogenes",
    "cicero",
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
)
ATTRIBUTION = re.compile(
    r"\b[Aa]s\s+(?:the\s+)?(?:[A-Z][\w'-]*\s+){0,3}[A-Z][\w'-]*\s+(?:once\s+)?"
    r"(?:said|says|put it|puts it|wrote|writes|taught|teaches|reminds us)\b"
)
WELLNESS_WORDS = (
    "hrv",
    "heart rate variability",
    "resting heart rate",
    "resting hr",
    "vo2",
    "daylight",
)
HARD_DAY_WORDS = ("streak", "next week")
EXCLAMATORY_OPENERS = re.compile(r"^(?:what\s+an?\b|how\s+\w+\b|such\s+an?\b|so\s+\w+\s*$)")

NUMBER = re.compile(r"(?<![A-Za-z0-9.])(\d+(?:\.\d+)?)(?![0-9.]*[0-9])")
THOUSANDS = re.compile(r"(?<=\d),(?=\d{3}\b)")
WORD = re.compile(r"[a-z0-9']+")
TOKEN = re.compile(r"\d+(?:\.\d+)?|[a-z']+")

CARDINALS = (
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
    "fifteen sixteen seventeen eighteen nineteen twenty"
).split()
ORDINAL_WORDS = (
    "zeroth first second third fourth fifth sixth seventh eighth ninth tenth eleventh twelfth "
    "thirteenth fourteenth fifteenth sixteenth seventeenth eighteenth nineteenth twentieth"
).split()
NUMBER_WORDS = {w: i for i, w in enumerate(CARDINALS) if i} | {
    w: i for i, w in enumerate(ORDINAL_WORDS) if i
}
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


@dataclass(frozen=True)
class GateResult:
    ok: bool
    reasons: tuple[str, ...] = field(default_factory=tuple)

    @property
    def reason(self) -> str:
        return "; ".join(self.reasons) if self.reasons else "pass"


def number_tokens(text: str) -> list[str]:
    return NUMBER.findall(THOUSANDS.sub("", text))


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
    }[kind]
    values = (_get(d, *path) for path in paths)
    return frozenset(
        float(v) for v in values if isinstance(v, int | float) and not isinstance(v, bool)
    )


def counted_numbers(text: str) -> list[tuple[str, float, str | None]]:
    """(token, value, kind) for every number word or digit sitting next to a counted noun."""
    tokens = TOKEN.findall(THOUSANDS.sub("", text.lower().replace("’", "'")))
    found: list[tuple[str, float, str | None]] = []
    for i, tok in enumerate(tokens):
        if tok in NUMBER_WORDS:
            value = float(NUMBER_WORDS[tok])
        elif tok[0].isdigit():
            value = float(tok)
        else:
            continue
        j = i + 1
        while j < len(tokens) and j <= i + 2 and tokens[j] in BETWEEN:
            j += 1
        if j < len(tokens) and tokens[j] in NOUN_KINDS:
            found.append((tok, value, NOUN_KINDS[tokens[j]]))
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
    return " ".join(WORD.findall(text.lower().replace("’", "'")))


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


def ancient_sources_in(text: str) -> list[str]:
    lowered = normalize(text)
    found = [name for name in ANCIENT_SOURCES if re.search(rf"\b{re.escape(name)}\b", lowered)]
    if "marcus aurelius" in found and "aurelius" in found:
        found.remove("aurelius")
    return found


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
    text = without_title(line, payload)
    bad = ungrounded_numbers(text, payload.numbers())
    for token, value, kind in counted_numbers(text):
        if value not in typed_numbers(payload, kind) and token not in bad:
            bad.append(token)
    return [f"grounding: {', '.join(bad)} not in payload"] if bad else []


def check_ban(line: str) -> list[str]:
    lowered = normalize(line)
    for label, pattern in BANS:
        if pattern.search(lowered):
            return [f"ban: '{label}'"]
    if "!" in line:
        return ["ban: exclamation mark"]
    emoji = [c for c in line if _is_emoji(c)]
    if emoji:
        return [f"ban: emoji {emoji[0]!r}"]
    exclamatory = [c for c in _clauses(line) if _exclamatory(c)]
    if len(exclamatory) >= 2:
        return ["ban: second exclamatory clause"]
    return []


def check_names(line: str, recent_sources: tuple[str, ...]) -> list[str]:
    if ATTRIBUTION.search(line):
        return ["names: attributed quote"]
    lowered = normalize(line)
    for name in MODERN_NAMES:
        if re.search(rf"\b{re.escape(normalize(name))}\b", lowered):
            return [f"names: living or modern person {name!r}"]
    named = ancient_sources_in(line)
    if named and recent_sources:
        return [f"names: {named[0]!r} named, a source was already named in the last 7 days"]
    if len(named) > 1:
        return ["names: more than one source in one line"]
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
        or check_names(text, recent.sources_named)
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
