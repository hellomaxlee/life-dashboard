"""Write parsed Health Auto Export records to SQLite. Every write is an upsert.

Activities are owned by clustering (`app.ingest.dedupe`): storing a copy re-clusters its
neighbourhood from scratch, so what is stored for a set of copies never depends on the order
they arrived. A payload is also authoritative for its source over the span of workouts it
carries: a stored copy inside that span that the payload no longer lists is withdrawn
(marked, never deleted), and a copy that reappears is un-withdrawn.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, replace
from datetime import timedelta
from statistics import fmean

from app.config import Settings
from app.ingest import dedupe
from app.ingest.dedupe import Copy
from app.ingest.parse import DailyValue, ParsedPayload, SleepSession, Workout, window_start_utc
from app.timeutil import from_utc_iso, now_utc, to_utc_iso

SAME_NIGHT_OVERLAP = 0.5


@dataclass
class IngestStats:
    workouts_seen: int = 0
    workouts_merged: int = 0
    workouts_withdrawn: int = 0
    metrics_rows: int = 0
    unknown_metrics: list[str] | None = None
    reapplied: int = 0


def _provenance(workout: Workout) -> dict[str, object]:
    return {
        "source_app": workout.source_app,
        "external_id": workout.external_id,
        "type": workout.type,
        "start_utc": workout.start_utc,
        "end_utc": workout.end_utc,
        "duration_s": workout.duration_s,
        "distance_m": workout.distance_m,
        "energy_kcal": workout.energy_kcal,
        "avg_hr": workout.avg_hr,
        "max_hr": workout.max_hr,
        "hr_sample_count": len(workout.hr_samples),
    }


def _copy_from_workout(workout: Workout) -> Copy:
    return Copy(
        external_id=workout.external_id,
        source_app=workout.source_app,
        start_utc=workout.start_utc,
        end_utc=workout.end_utc,
        duration_s=workout.duration_s,
        hr_sample_count=len(workout.hr_samples),
        withdrawn_at=None,
        provenance=_provenance(workout),
    )


def _copy_from_row(conn: sqlite3.Connection, row: sqlite3.Row) -> Copy:
    """A stored copy. A row written before copies kept their own provenance (migration 003
    found no entry for it) is described from what the row and its samples say."""
    prov = json.loads(row["provenance_json"] or "{}")
    if not prov:
        activity = conn.execute(
            "SELECT type FROM activities WHERE id = ?", (row["activity_id"],)
        ).fetchone()
        samples = conn.execute(
            "SELECT COUNT(DISTINCT ts_utc) FROM workout_hr_samples "
            "WHERE external_id = ? AND source_app = ?",
            (row["external_id"], row["source_app"]),
        ).fetchone()[0]
        span = from_utc_iso(row["end_utc"]) - from_utc_iso(row["start_utc"])
        prov = {
            "source_app": row["source_app"],
            "external_id": row["external_id"],
            "type": activity["type"] if activity else "Workout",
            "start_utc": row["start_utc"],
            "end_utc": row["end_utc"],
            "duration_s": int(span.total_seconds()),
            "distance_m": None,
            "energy_kcal": None,
            "avg_hr": None,
            "max_hr": None,
            "hr_sample_count": int(samples),
        }
    return Copy(
        external_id=str(row["external_id"]),
        source_app=str(row["source_app"]),
        start_utc=str(row["start_utc"]),
        end_utc=str(row["end_utc"]),
        duration_s=int(prov["duration_s"]),
        hr_sample_count=int(prov.get("hr_sample_count") or 0),
        withdrawn_at=row["withdrawn_at"],
        provenance=prov,
    )


def _neighbourhood(conn: sqlite3.Connection, copy: Copy, settings: Settings) -> dict[tuple, Copy]:
    """Every stored copy that could share an activity with `copy`, with every other copy of
    those activities (whole clusters, so a re-cluster can only ever split or join them), and
    every copy that shares its external id (so activity ids stay unique)."""
    reach = timedelta(minutes=settings.ingest.dedupe.start_window_min)
    lo = to_utc_iso(from_utc_iso(copy.start_utc) - reach)
    hi = to_utc_iso(from_utc_iso(copy.end_utc) + reach)
    seeds = conn.execute(
        "SELECT activity_id FROM activity_sources WHERE start_utc <= ? AND end_utc >= ? "
        "UNION SELECT activity_id FROM activity_sources WHERE external_id = ?",
        (hi, lo, copy.external_id),
    ).fetchall()
    ids = [s["activity_id"] for s in seeds]
    if not ids:
        return {}
    rows = conn.execute(
        f"SELECT * FROM activity_sources WHERE activity_id IN ({','.join('?' * len(ids))}) "
        "ORDER BY external_id, source_app",
        ids,
    ).fetchall()
    return {(r["external_id"], r["source_app"]): _copy_from_row(conn, r) for r in rows}


def _cluster_id(conn: sqlite3.Connection, members: list[Copy], everyone: list[Copy]) -> str:
    """The canonical copy's external id; suffixed with its source app when any copy stored
    or being stored from another source carries the same id, so two clusters never share an
    id. A copy belongs to exactly one cluster, so no other cluster can claim the id."""
    canonical = members[0]
    shared = any(
        c.external_id == canonical.external_id and c.source_app != canonical.source_app
        for c in everyone
    ) or (
        conn.execute(
            "SELECT 1 FROM activity_sources WHERE external_id = ? AND source_app <> ?",
            canonical.key,
        ).fetchone()
        is not None
    )
    return f"{canonical.external_id}|{canonical.source_app}" if shared else canonical.external_id


def _hr_fields(
    conn: sqlite3.Connection, members: list[Copy]
) -> tuple[float | None, float | None, int, int]:
    """HR from the members' samples: the sample source with the most distinct timestamps
    wins; a timestamp reported by two copies of that source counts once."""
    keys = ", ".join("(?, ?)" for _ in members)
    params = [v for m in members for v in m.key]
    where = f"(external_id, source_app) IN (VALUES {keys})"
    counts = conn.execute(
        f"SELECT source, COUNT(DISTINCT ts_utc) AS n, MIN(ts_utc) AS first_ts, "
        f"MAX(ts_utc) AS last_ts FROM workout_hr_samples WHERE {where} "
        "GROUP BY source ORDER BY n DESC, source",
        params,
    ).fetchall()
    if not counts:
        avg = next((m.provenance["avg_hr"] for m in members if m.provenance.get("avg_hr")), None)
        mx = next((m.provenance["max_hr"] for m in members if m.provenance.get("max_hr")), None)
        return avg, mx, 0, 0
    winner = counts[0]
    samples = conn.execute(
        f"SELECT AVG(bpm_avg) AS bpm_avg, MAX(bpm_max) AS bpm_max FROM workout_hr_samples "
        f"WHERE {where} AND source = ? GROUP BY ts_utc",
        [*params, winner["source"]],
    ).fetchall()
    avgs = [s["bpm_avg"] for s in samples if s["bpm_avg"] is not None]
    maxes = [s["bpm_max"] for s in samples if s["bpm_max"] is not None]
    avg_hr = fmean(avgs) if avgs else None
    max_hr = max(maxes) if maxes else None
    span = int((from_utc_iso(winner["last_ts"]) - from_utc_iso(winner["first_ts"])).total_seconds())
    return avg_hr, max_hr, int(winner["n"]), span


def _max_or_none(values: list[float | None]) -> float | None:
    present = [v for v in values if v is not None]
    return max(present) if present else None


def _write_activity(
    conn: sqlite3.Connection, activity_id: str, members: list[Copy], settings: Settings
) -> None:
    """Derive every stored field of an activity from its member copies and their samples."""
    canonical = members[0].provenance
    entries = [m.provenance for m in members]
    avg_hr, max_hr, count, span = _hr_fields(conn, members)
    duration_s = int(canonical["duration_s"])
    incomplete = int(count == 0 or span < settings.ingest.hr_incomplete_ratio * duration_s)
    withdrawn_at = None if members[0].active else max(m.withdrawn_at or "" for m in members)
    conn.execute(
        "UPDATE activities SET type = ?, start_utc = ?, end_utc = ?, duration_s = ?, "
        "distance_m = ?, energy_kcal = ?, avg_hr = ?, max_hr = ?, hr_sample_count = ?, "
        "hr_span_s = ?, hr_incomplete = ?, merged_from_json = ?, withdrawn_at = ? WHERE id = ?",
        (
            canonical["type"],
            canonical["start_utc"],
            canonical["end_utc"],
            duration_s,
            _max_or_none([e.get("distance_m") for e in entries]),
            _max_or_none([e.get("energy_kcal") for e in entries]),
            avg_hr,
            max_hr,
            count,
            span,
            incomplete,
            json.dumps(entries, sort_keys=True),
            withdrawn_at,
            activity_id,
        ),
    )


def _rebuild(
    conn: sqlite3.Connection,
    copies: list[Copy],
    settings: Settings,
    old_ids: set[str],
    fresh: tuple[Workout, int | None] | None = None,
) -> list[list[Copy]]:
    """Re-cluster `copies` from scratch and make the stored activities match: rows are
    upserted under their cluster ids, every copy and its samples re-pointed, and ids no
    cluster claims any more deleted. `fresh` is the workout being stored, if any."""
    clusters = dedupe.cluster(copies, settings.ingest.dedupe)
    new_ids: dict[str, list[Copy]] = {}
    for members in clusters:
        canonical = members[0]
        cid = _cluster_id(conn, members, copies)
        new_ids[cid] = members
        conn.execute(
            "INSERT INTO activities (id, type, start_utc, end_utc, duration_s, merged_from_json, "
            "withdrawn_at) VALUES (?, ?, ?, ?, ?, '[]', NULL) ON CONFLICT (id) DO NOTHING",
            (
                cid,
                canonical.provenance["type"],
                canonical.start_utc,
                canonical.end_utc,
                canonical.duration_s,
            ),
        )
        for m in members:
            conn.execute(
                "INSERT INTO activity_sources (external_id, source_app, activity_id, start_utc, "
                "end_utc, raw_archive_id, withdrawn_at, provenance_json) "
                "VALUES (?, ?, ?, ?, ?, NULL, ?, ?) ON CONFLICT (external_id, source_app) "
                "DO UPDATE SET activity_id = excluded.activity_id, start_utc = excluded.start_utc, "
                "end_utc = excluded.end_utc, withdrawn_at = excluded.withdrawn_at, "
                "provenance_json = excluded.provenance_json",
                (
                    m.external_id,
                    m.source_app,
                    cid,
                    m.start_utc,
                    m.end_utc,
                    m.withdrawn_at,
                    json.dumps(m.provenance, sort_keys=True),
                ),
            )
    stale = sorted(old_ids - set(new_ids))
    if stale:
        conn.execute(f"DELETE FROM activities WHERE id IN ({','.join('?' * len(stale))})", stale)
    if fresh is not None:
        workout, raw_archive_id = fresh
        conn.execute(
            "UPDATE activity_sources SET raw_archive_id = ? "
            "WHERE external_id = ? AND source_app = ?",
            (raw_archive_id, workout.external_id, workout.source_app),
        )
        conn.executemany(
            "INSERT OR REPLACE INTO workout_hr_samples "
            "(external_id, source_app, ts_utc, bpm_min, bpm_avg, bpm_max, source) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    workout.external_id,
                    workout.source_app,
                    s.ts_utc,
                    s.bpm_min,
                    s.bpm_avg,
                    s.bpm_max,
                    s.source,
                )
                for s in workout.hr_samples
            ],
        )
    for cid, members in new_ids.items():
        _write_activity(conn, cid, members, settings)
    return clusters


def upsert_workout(
    conn: sqlite3.Connection, workout: Workout, raw_archive_id: int | None, settings: Settings
) -> bool:
    """Store one copy and re-cluster its neighbourhood. Returns True when a copy seen for the
    first time shares its activity with another copy. A withdrawn copy that is reported
    again is active again."""
    fresh = _copy_from_workout(workout)
    stored = _neighbourhood(conn, fresh, settings)
    known = fresh.key in stored
    old_ids = _activity_ids_of(conn, list(stored.values()))
    stored[fresh.key] = fresh
    clusters = _rebuild(conn, list(stored.values()), settings, old_ids, (workout, raw_archive_id))
    mine = next(members for members in clusters if any(m.key == fresh.key for m in members))
    return not known and len(mine) > 1


def _activity_ids_of(conn: sqlite3.Connection, copies: list[Copy]) -> set[str]:
    if not copies:
        return set()
    keys = ", ".join("(?, ?)" for _ in copies)
    rows = conn.execute(
        "SELECT DISTINCT activity_id FROM activity_sources "
        f"WHERE (external_id, source_app) IN (VALUES {keys})",
        [v for c in copies for v in c.key],
    ).fetchall()
    return {r["activity_id"] for r in rows}


def withdraw_absent(
    conn: sqlite3.Connection, workouts: list[Workout], withdrawn_at: str, settings: Settings
) -> int:
    """A payload is authoritative, for each source app it carries a workout from, over the
    span it demonstrably covers: from its earliest to its latest workout start (any source).
    A stored active copy of such a source that starts inside the span and that the payload
    does not list is withdrawn. A payload with no workouts withdraws nothing; a source with
    no workout in the payload loses nothing. Returns how many copies were newly withdrawn.

    `withdrawn_at` is the receipt time of the EARLIEST payload that withdrew the copy since
    it was last listed. A late recovery applies an older payload on top of newer ones, so
    keeping the earliest (not the first applied) is what makes recovery equal the history
    without a failure."""
    if not workouts:
        return 0
    listed = {(w.external_id, w.source_app) for w in workouts}
    sources = sorted({w.source_app for w in workouts})
    lo = min(w.start_utc for w in workouts)
    hi = max(w.start_utc for w in workouts)
    rows = conn.execute(
        f"SELECT * FROM activity_sources WHERE source_app IN ({','.join('?' * len(sources))}) "
        "AND start_utc BETWEEN ? AND ? AND (withdrawn_at IS NULL OR withdrawn_at > ?) "
        "ORDER BY start_utc, external_id, source_app",
        (*sources, lo, hi, withdrawn_at),
    ).fetchall()
    withdrawn = 0
    for row in rows:
        if (row["external_id"], row["source_app"]) in listed:
            continue
        gone = replace(_copy_from_row(conn, row), withdrawn_at=withdrawn_at)
        conn.execute(
            "UPDATE activity_sources SET withdrawn_at = ? WHERE external_id = ? AND source_app = ?",
            (withdrawn_at, *gone.key),
        )
        stored = _neighbourhood(conn, gone, settings)
        stored[gone.key] = gone
        copies = list(stored.values())
        _rebuild(conn, copies, settings, _activity_ids_of(conn, copies))
        withdrawn += row["withdrawn_at"] is None
    return withdrawn


def recluster_all(conn: sqlite3.Connection, settings: Settings) -> int:
    """Bring every stored activity to the current clustering and canonical rule; returns how
    many activity rows are new or changed. Idempotent."""
    before = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM activities")}
    rows = conn.execute("SELECT * FROM activity_sources ORDER BY external_id, source_app")
    copies = [_copy_from_row(conn, r) for r in rows.fetchall()]
    _rebuild(conn, copies, settings, set(before))
    after = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM activities")}
    return sum(before.get(k) != row for k, row in after.items())


def sleep_id(session: SleepSession) -> str:
    key = f"{session.start_utc}|{session.end_utc}|{session.source}".encode()
    return "sleep_" + hashlib.sha256(key).hexdigest()[:16]


def _same_night(a_start: str, a_end: str, b_start: str, b_end: str) -> bool:
    """Two sessions are one night when they overlap by at least half of the shorter."""
    first_end, last_start = from_utc_iso(min(a_end, b_end)), from_utc_iso(max(a_start, b_start))
    overlap = (first_end - last_start).total_seconds()
    if overlap <= 0:
        return False
    shorter = min(
        (from_utc_iso(a_end) - from_utc_iso(a_start)).total_seconds(),
        (from_utc_iso(b_end) - from_utc_iso(b_start)).total_seconds(),
    )
    return shorter <= 0 or overlap >= SAME_NIGHT_OVERLAP * shorter


def _night_preference(session: SleepSession) -> tuple:
    return (-(session.asleep_s or 0), -(session.in_bed_s or 0), session.source, session.start_utc)


def one_night_per_person(sessions: list[SleepSession]) -> list[SleepSession]:
    """Of the sessions one payload carries under different source labels for the same night,
    keep the fullest (most asleep, then longest in bed, then the lowest label)."""
    kept: list[SleepSession] = []
    for session in sorted(sessions, key=_night_preference):
        if any(
            k.source != session.source
            and _same_night(k.start_utc, k.end_utc, session.start_utc, session.end_utc)
            for k in kept
        ):
            continue
        kept.append(session)
    return kept


def _clipped_by_window(
    conn: sqlite3.Connection, session: SleepSession, window_start_utc: str | None
) -> list[str]:
    """Ids of stored sessions of the same wake day that began before the payload's window and
    overlap this one (same source), or are the same night (another label): fuller reports
    of the night the window cut."""
    if window_start_utc is None or session.start_utc < window_start_utc:
        return []
    rows = conn.execute(
        "SELECT id, source, start_utc, end_utc FROM sleep_sessions "
        "WHERE wake_day_local = ? AND start_utc < ? AND end_utc > ?",
        (session.wake_day_local, window_start_utc, session.start_utc),
    ).fetchall()
    return [
        str(r["id"])
        for r in rows
        if r["source"] == session.source
        or _same_night(r["start_utc"], r["end_utc"], session.start_utc, session.end_utc)
    ]


def _evict_other_labels(conn: sqlite3.Connection, session: SleepSession, spared: list[str]) -> None:
    """One person, one night: a stored session under another label that is the same night
    as the incoming one loses to it (last received wins)."""
    rows = conn.execute(
        "SELECT id, start_utc, end_utc FROM sleep_sessions WHERE source <> ? "
        "AND start_utc < ? AND end_utc > ?",
        (session.source, session.end_utc, session.start_utc),
    ).fetchall()
    losers = [
        r["id"]
        for r in rows
        if r["id"] not in spared
        and _same_night(r["start_utc"], r["end_utc"], session.start_utc, session.end_utc)
    ]
    for loser in losers:
        conn.execute("DELETE FROM sleep_sessions WHERE id = ?", (loser,))


def replace_sleep(
    conn: sqlite3.Connection, sessions: list[SleepSession], window_start_utc: str | None = None
) -> None:
    """A payload is authoritative for each (wake day, source) it carries: that day's sessions
    from that source are replaced by the payload's, so a corrected night leaves one row, and
    a short session the source no longer reports is gone. A night the payload carries under
    two labels is stored once; a stored night under another label gives way to it.

    One exception. The export's date window opens at midnight of its first day, so a night
    that began the evening before arrives cut at midnight (seen on every real 7-day push:
    the first segment starts a few minutes past 00:00). A cut night never replaces a stored
    session of the same wake day that began before the window and overlaps it, whatever its
    label; the stored one is the same night, reported whole by an earlier push.
    """
    nights = one_night_per_person(sessions)
    keep: set[str] = set()
    fresh: list[SleepSession] = []
    for session in nights:
        clipped_by = _clipped_by_window(conn, session, window_start_utc)
        if clipped_by:
            keep.update(clipped_by)
        else:
            fresh.append(session)
    kept = sorted(keep)
    spared = f" AND id NOT IN ({','.join('?' * len(kept))})" if kept else ""
    for wake_day, source in sorted({(s.wake_day_local, s.source) for s in sessions}):
        conn.execute(
            f"DELETE FROM sleep_sessions WHERE wake_day_local = ? AND source = ?{spared}",
            (wake_day, source, *kept),
        )
    for session in fresh:
        _evict_other_labels(conn, session, kept)
        upsert_sleep(conn, session)


def upsert_sleep(conn: sqlite3.Connection, session: SleepSession) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO sleep_sessions (id, wake_day_local, start_utc, end_utc, "
        "in_bed_s, asleep_s, core_s, deep_s, rem_s, awake_s, source) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            sleep_id(session),
            session.wake_day_local,
            session.start_utc,
            session.end_utc,
            session.in_bed_s,
            session.asleep_s,
            session.core_s,
            session.deep_s,
            session.rem_s,
            session.awake_s,
            session.source,
        ),
    )


def upsert_steps(conn: sqlite3.Connection, value: DailyValue) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO steps_daily (day_local, steps, source) VALUES (?, ?, ?)",
        (value.day_local, int(round(value.value)), value.source),
    )


def upsert_wellness(conn: sqlite3.Connection, value: DailyValue) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO wellness_daily (day_local, metric, value, units, source) "
        "VALUES (?, ?, ?, ?, ?)",
        (value.day_local, value.metric, value.value, value.units, value.source),
    )


def _received_at(conn: sqlite3.Connection, raw_archive_id: int | None) -> str:
    """When the payload arrived, from its archive row, so a replay withdraws at the same
    instant the live service did."""
    if raw_archive_id is not None:
        row = conn.execute(
            "SELECT received_at_utc FROM raw_archive WHERE id = ?", (raw_archive_id,)
        ).fetchone()
        if row is not None:
            return str(row["received_at_utc"])
    return to_utc_iso(now_utc())


def store_payload(
    conn: sqlite3.Connection,
    parsed: ParsedPayload,
    raw_archive_id: int | None,
    settings: Settings,
    record_log: bool = True,
) -> IngestStats:
    stats = IngestStats(unknown_metrics=list(parsed.unknown_metrics))
    workouts = sorted(parsed.workouts, key=lambda w: (w.start_utc, w.external_id, w.source_app))
    for workout in workouts:
        stats.workouts_seen += 1
        if upsert_workout(conn, workout, raw_archive_id, settings):
            stats.workouts_merged += 1
    if workouts:
        received = _received_at(conn, raw_archive_id)
        stats.workouts_withdrawn = withdraw_absent(conn, workouts, received, settings)
    replace_sleep(conn, parsed.sleep, window_start_utc(parsed, settings.home_tz))
    for value in parsed.steps:
        upsert_steps(conn, value)
    for value in parsed.wellness:
        upsert_wellness(conn, value)
    stats.metrics_rows = len(parsed.sleep) + len(parsed.steps) + len(parsed.wellness)
    if raw_archive_id is not None and record_log:
        conn.execute(
            "INSERT INTO ingest_log "
            "(raw_archive_id, workouts_seen, workouts_merged, metrics_rows, unknown_metrics) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                raw_archive_id,
                stats.workouts_seen,
                stats.workouts_merged,
                stats.metrics_rows,
                json.dumps(stats.unknown_metrics),
            ),
        )
    return stats
