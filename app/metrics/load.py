"""Training load: daily TRIMP and the acute / chronic EWMA (notes.txt § Goal model,
"Training load").

The day's TRIMP is the sum of the Edwards loads of its workouts (the HR-based TRIMP the goal
model names; Edwards is the HR-based variant already chosen for effort load). The EWMA uses
lambda = 2 / (N + 1) with N = 7 (acute) and N = 42 (chronic), runs over every day including
rest days (load 0), and starts from 0 the day before the first stored day. Balance is
acute / chronic, null while chronic is 0 or fewer than `BALANCE_MIN_DAYS` days exist.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.metrics.keys import round_half_up

ACUTE_DAYS = 7
CHRONIC_DAYS = 42
BALANCE_MIN_DAYS = 28


@dataclass(frozen=True)
class LoadRow:
    trimp: float
    acute: float
    chronic: float
    balance: float | None


def ewma_lambda(days: int) -> float:
    return 2.0 / (days + 1)


def load_rows(trimps: list[float]) -> list[LoadRow]:
    """One row per day, in the order given (chronological, contiguous)."""
    acute_l, chronic_l = ewma_lambda(ACUTE_DAYS), ewma_lambda(CHRONIC_DAYS)
    acute = chronic = 0.0
    rows: list[LoadRow] = []
    for index, trimp in enumerate(trimps):
        acute = acute_l * trimp + (1 - acute_l) * acute
        chronic = chronic_l * trimp + (1 - chronic_l) * chronic
        enough = index + 1 >= BALANCE_MIN_DAYS and chronic > 0
        balance = round_half_up(acute / chronic, 2) if enough else None
        rows.append(
            LoadRow(
                round_half_up(trimp, 1),
                round_half_up(acute, 2),
                round_half_up(chronic, 2),
                balance,
            )
        )
    return rows
