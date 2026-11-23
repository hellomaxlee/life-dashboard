"""Daily small wins (notes.txt § Goal model, "Daily small wins"). Steps feed nothing."""

from __future__ import annotations

WIN_ORDER = ("sleep", "workout", "book")


def shown_hours(sleep_hours: float) -> float:
    """One decimal, truncated: the number the frame and the summary show."""
    return int(sleep_hours * 10 + 1e-6) / 10


def overslept(sleep_hours: float | None, max_hours: float) -> bool:
    """More than the upper bound as shown, so a night that reads 9.0 is never over 9."""
    return sleep_hours is not None and shown_hours(sleep_hours) > max_hours


def sleep_win(sleep_hours: float | None, target_hours: float, max_hours: float) -> bool:
    """A night inside the band: at the target or more, and not over the upper bound."""
    if sleep_hours is None or overslept(sleep_hours, max_hours):
        return False
    return sleep_hours >= target_hours


def wins(sleep: bool, workout: bool, book: bool) -> list[str]:
    flags = {"sleep": sleep, "workout": workout, "book": book}
    return [name for name in WIN_ORDER if flags[name]]
