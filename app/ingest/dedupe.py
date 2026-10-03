"""One canonical activity: clustering is a pure function of the set of copies.

A copy is one report of a workout by one HealthKit source app (`external_id`, `source_app`).
Two copies are the same workout when the pairwise rule holds (notes.txt § Goal model, Dedupe):

- from different source apps (or either `unknown`, a copy with no time series to name it):
  starts within `start_window_min` minutes and durations within `duration_tolerance_pct`;
- from the same source app: their time intervals genuinely overlap. One device cannot record
  two simultaneous workouts, so an overlap is the app re-issuing the workout under a new id;
  two back-to-back short workouts from one device never merge.

An activity is a connected component of that relation over active (not withdrawn) copies;
withdrawn copies cluster among themselves the same way and never link to an active copy.
Within a cluster the canonical copy has the most HR samples, then the earliest start, then the
lowest external id; it names and describes the activity. Arrival order plays no part.

Setting DEDUPE_DISABLED=1 in the environment makes every copy its own cluster; it is the
mutant the source-replay skill runs to prove the dedupe test is a real gate.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import timedelta

from app.config import DedupeConfig
from app.ingest.parse import UNKNOWN_SOURCE
from app.timeutil import from_utc_iso


def disabled() -> bool:
    return os.environ.get("DEDUPE_DISABLED", "") == "1"


@dataclass(frozen=True)
class Copy:
    external_id: str
    source_app: str
    start_utc: str
    end_utc: str
    duration_s: int
    hr_sample_count: int
    withdrawn_at: str | None
    provenance: dict

    @property
    def key(self) -> tuple[str, str]:
        return (self.external_id, self.source_app)

    @property
    def active(self) -> bool:
        return self.withdrawn_at is None


def copy_rank(copy: Copy) -> tuple:
    """Order of the copies of one activity: most HR samples, then earliest start, then
    lowest external id. The first is the canonical copy."""
    return (-copy.hr_sample_count, copy.start_utc, copy.external_id, copy.source_app)


def durations_match(a_s: int, b_s: int, tolerance_pct: float) -> bool:
    longest = max(a_s, b_s)
    if longest <= 0:
        return a_s == b_s
    return abs(a_s - b_s) / longest * 100.0 <= tolerance_pct


def intervals_overlap(a: Copy, b: Copy) -> bool:
    return a.start_utc < b.end_utc and b.start_utc < a.end_utc


def same_workout(a: Copy, b: Copy, cfg: DedupeConfig) -> bool:
    """The pairwise rule. Symmetric; never links an active copy with a withdrawn one."""
    if a.active != b.active:
        return False
    if a.source_app == b.source_app and a.source_app != UNKNOWN_SOURCE:
        return intervals_overlap(a, b)
    gap = abs(from_utc_iso(a.start_utc) - from_utc_iso(b.start_utc))
    if gap > timedelta(minutes=cfg.start_window_min):
        return False
    return durations_match(a.duration_s, b.duration_s, cfg.duration_tolerance_pct)


def cluster(copies: list[Copy], cfg: DedupeConfig) -> list[list[Copy]]:
    """Connected components of `same_workout`, each in `copy_rank` order, components in
    order of their canonical copy. Depends only on the set of copies."""
    ordered = sorted(copies, key=lambda c: (c.start_utc, c.external_id, c.source_app))
    parent = list(range(len(ordered)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    if not disabled():
        reach = timedelta(minutes=cfg.start_window_min)
        for i, a in enumerate(ordered):
            horizon = max(reach, timedelta(seconds=a.duration_s))
            for j in range(i + 1, len(ordered)):
                b = ordered[j]
                if from_utc_iso(b.start_utc) - from_utc_iso(a.start_utc) > horizon:
                    break
                if same_workout(a, b, cfg):
                    parent[find(j)] = find(i)
    groups: dict[int, list[Copy]] = {}
    for i, c in enumerate(ordered):
        groups.setdefault(find(i), []).append(c)
    members = [sorted(g, key=copy_rank) for g in groups.values()]
    return sorted(members, key=lambda g: copy_rank(g[0]))
