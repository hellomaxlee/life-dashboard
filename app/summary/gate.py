"""The summary gate. A line passes only if every rule below passes; the first failing rule
names the reason. The same gate judges the model's line and the fallback's.

Rules, in order:
  length      the device line is 1 to 110 characters, no control characters
  grounding   every number token (digits, optional decimals, thousands commas removed) equals
              a number in the payload: "7.4" is 7.4, "118" is 118 or 118.0, "7" is 7.0
  ban         a ban-list phrase (case-insensitive, word-bounded), "only N left/more",
              any "!", any emoji or symbol character, a second exclamatory clause
  names       an attributed quote ("as X said/says/wrote/put it"), a modern name, or an
              ancient source named when one was already named in the last seven days
  hard-days   after a broken week: no "streak", no "next week"
  wellness    HRV, resting heart rate, VO2 max, daylight only when the payload has a fact
  opening     the first three words do not open any of the last 14 lines
  similarity  character-trigram Jaccard against each of the last 30 lines is below the
              configured threshold
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

BAN_PHRASES = (
    "don't break the chain",
    "dont break the chain",
    "break the chain",
    "don't let the streak",
    "streak die",
    "keep the streak alive",
    "no excuses",
    "crush",
    "grind",
    "beast",
    "lazy",
    "should have",
    "should've",
    "you failed",
    "disappoint",
    "stay hard",
    "no days off",
    "hustle",
    "guilt",
    "shame",
    "you need to",
    "you must",
    "just do it",
    "slacked",
)
ONLY_N_LEFT = re.compile(r"\bonly\s+(?:\d+|one|two|three|a few|another)\s+(?:more|left|to go)\b")

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
)
ATTRIBUTION = re.compile(
    r"\b[Aa]s\s+(?:[A-Z][\w'-]*\s+){0,3}[A-Z][\w'-]*\s+(?:once\s+)?"
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


def check_length(line: str) -> list[str]:
    if not line.strip():
        return ["length: empty line"]
    if len(line) > DEVICE_MAX:
        return [f"length: {len(line)} chars, limit {DEVICE_MAX}"]
    if any(unicodedata.category(c)[0] == "C" for c in line):
        return ["length: control character"]
    return []


def check_grounding(line: str, payload: Payload) -> list[str]:
    bad = ungrounded_numbers(line, payload.numbers())
    return [f"grounding: {', '.join(bad)} not in payload"] if bad else []


def check_ban(line: str) -> list[str]:
    lowered = normalize(line)
    for phrase in BAN_PHRASES:
        if re.search(rf"\b{re.escape(normalize(phrase))}\b", lowered):
            return [f"ban: '{phrase}'"]
    if ONLY_N_LEFT.search(lowered):
        return ["ban: 'only N left'"]
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


def check_device_line(line: str, payload: Payload, recent: Recent, threshold: float) -> GateResult:
    reasons = (
        check_length(line)
        or check_grounding(line, payload)
        or check_ban(line)
        or check_names(line, recent.sources_named)
        or check_hard_day(line, payload)
        or check_wellness(line, payload)
        or check_opening(line, recent)
        or check_similarity(line, recent, threshold)
    )
    return GateResult(not reasons, tuple(reasons))


def check_web_line(line: str, payload: Payload, recent: Recent) -> GateResult:
    reasons = (
        check_grounding(line, payload)
        or check_ban(line)
        or check_names(line, recent.sources_named)
        or check_hard_day(line, payload)
        or check_wellness(line, payload)
    )
    return GateResult(not reasons, tuple(reasons))
