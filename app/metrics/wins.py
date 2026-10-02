"""Daily small wins (notes.txt § Goal model, "Daily small wins"). Steps feed nothing."""

from __future__ import annotations

WIN_ORDER = ("sleep", "workout", "book")


def sleep_win(sleep_hours: float | None, target_hours: float) -> bool:
    return sleep_hours is not None and sleep_hours >= target_hours


def wins(sleep: bool, workout: bool, book: bool) -> list[str]:
    flags = {"sleep": sleep, "workout": workout, "book": book}
    return [name for name in WIN_ORDER if flags[name]]
