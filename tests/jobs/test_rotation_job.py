"""The device rotation job against fake adapters and a fake transport. No device is owned."""

from __future__ import annotations

import json
import socket
import threading
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import DeviceConfig, load_settings
from app.db import open_db
from app.jobs import rotation as rotation_job
from app.jobs import scheduler as jobs
from app.jobs.rotation import HOLDING, ROTATION_JOB, DeviceRotation, device_adapter
from app.main import create_app
from app.render.adapters.pixoo import PixooAdapter, PixooError
from app.render.celebrate import sparkle_clip
from app.render.frame import Clip
from app.render.rotation import (
    ROTATION_ORDER,
    render_screen,
    rotation_clips,
    rotation_sequence,
    sequence_names,
)
from app.render.view_db import view_from_db
from app.timeutil import local_day

HOST = "192.168.1.50"
NOON = datetime(2026, 10, 2, 16, 0, tzinfo=UTC)
LONG_SUMMARY = (
    "Three good sessions this week and a full night of sleep; the steady work is adding up, "
    "so today can be easy."
)


class Clock:
    def __init__(self, now: datetime = NOON) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class FakeAdapter:
    def __init__(self, fail_on_sends: tuple[int, ...] = ()) -> None:
        self.sent: list[Clip] = []
        self.attempts = 0
        self._fail_on_sends = fail_on_sends

    def send(self, clip: Clip) -> None:
        self.attempts += 1
        if self.attempts in self._fail_on_sends:
            raise PixooError("Draw/GetHttpGifId: timed out")
        self.sent.append(clip)


def with_device(settings, seconds: int = 20, host: str = HOST):
    return replace(settings, device=DeviceConfig(pixoo_host=host, screen_seconds=seconds))


def opener(settings):
    return lambda: open_db(settings.storage.db_path)


def same(a: Clip, b: Clip) -> bool:
    return a.durations_ms == b.durations_ms and [f.tobytes() for f in a.frames] == [
        f.tobytes() for f in b.frames
    ]


def set_day(db, day: str, **metrics: object) -> None:
    db.execute("INSERT INTO daily_metrics VALUES (?, ?)", (day, json.dumps(metrics)))
    db.commit()


def fake_device(seen: list[httpx.Request]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"error_code": 0, "PicId": 7})

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_ticks_walk_the_rotation_in_order_and_wrap(db, jobs_settings):
    clock, adapter = Clock(), FakeAdapter()
    rotation = DeviceRotation(jobs_settings, opener(jobs_settings), adapter, clock)
    shown = []
    for _ in range(7):
        shown.append(rotation.tick())
        clock.now += timedelta(seconds=20)
    assert shown == [*ROTATION_ORDER, *ROTATION_ORDER, ROTATION_ORDER[0]]
    assert shown[:3] == ["week", "today", "books"]

    view = view_from_db(db, jobs_settings, "2026-10-02")
    assert [name for name, _ in rotation_clips(view, NOON)] == shown[:3]
    for index, name in enumerate(shown):
        at = NOON + timedelta(seconds=20 * index)
        assert same(adapter.sent[index], render_screen(name, view, at))


def test_each_tick_uses_a_fresh_connection_and_closes_it(db, jobs_settings):
    conns = []

    def open_conn():
        conns.append(open_db(jobs_settings.storage.db_path))
        return conns[-1]

    clock = Clock()
    rotation = DeviceRotation(jobs_settings, open_conn, FakeAdapter(), clock)
    for _ in range(2):
        rotation.tick()
        clock.now += timedelta(seconds=20)
    assert len(conns) == 2 and conns[0] is not conns[1]
    for conn in conns:
        with pytest.raises(Exception, match="closed"):
            conn.execute("SELECT 1")


def test_the_day_shown_is_the_new_york_day_on_both_sides_of_midnight(db, jobs_settings):
    set_day(db, "2026-10-01", steps=1111)
    set_day(db, "2026-10-02", steps=22222)
    before = datetime(2026, 10, 2, 3, 59, 39, tzinfo=UTC)
    after = datetime(2026, 10, 2, 3, 59, 40, tzinfo=UTC)
    expected = {
        day: render_screen("today", view_from_db(db, jobs_settings, day), NOON)
        for day in ("2026-10-01", "2026-10-02")
    }
    assert not same(expected["2026-10-01"], expected["2026-10-02"])

    for moment, day in ((before, "2026-10-01"), (after, "2026-10-02")):
        clock, adapter = Clock(moment), FakeAdapter()
        rotation = DeviceRotation(jobs_settings, opener(jobs_settings), adapter, clock)
        assert rotation.tick() == "week"
        clock.now += timedelta(seconds=20)
        assert rotation.tick() == "today"
        assert same(adapter.sent[1], expected[day])


def test_an_adapter_error_costs_one_slot_and_the_next_tick_shows_the_next_screen(
    db, jobs_settings, caplog
):
    adapter = FakeAdapter(fail_on_sends=(1, 2))
    stats: dict[str, jobs.JobStats] = {}
    clock = Clock()
    rotation = DeviceRotation(jobs_settings, opener(jobs_settings), adapter, clock)
    run = jobs.guarded(ROTATION_JOB, rotation.tick, stats)

    run()
    clock.now = rotation.due()
    run()
    assert adapter.sent == []
    assert stats[ROTATION_JOB] == jobs.JobStats(2, 2, "PixooError: Draw/GetHttpGifId: timed out")
    assert f"job {ROTATION_JOB} failed" in caplog.text

    clock.now = rotation.due()
    run()
    view = view_from_db(db, jobs_settings, "2026-10-02")
    assert same(adapter.sent[0], render_screen("books", view, NOON))
    assert stats[ROTATION_JOB] == jobs.JobStats(runs=3, failures=2, last_error=None)
    clock.now += timedelta(seconds=20)
    assert rotation.tick() == "week"


def test_a_screen_that_cannot_render_does_not_stop_the_others(db, jobs_settings, monkeypatch):
    def broken_today(name, view, now):
        if name == "today":
            raise ZeroDivisionError("layout bug")
        return render_screen(name, view, now)

    monkeypatch.setattr(rotation_job, "render_screen", broken_today)
    adapter, clock = FakeAdapter(), Clock()
    stats: dict[str, jobs.JobStats] = {}
    rotation = DeviceRotation(jobs_settings, opener(jobs_settings), adapter, clock)
    run = jobs.guarded(ROTATION_JOB, rotation.tick, stats)
    for _ in range(6):
        run()
        clock.now += timedelta(seconds=20)
    assert (stats[ROTATION_JOB].runs, stats[ROTATION_JOB].failures) == (6, 2)
    assert adapter.attempts == len(adapter.sent) == 4


def test_a_clip_longer_than_the_dwell_keeps_its_screen_until_it_has_played_once(db, jobs_settings):
    set_day(db, "2026-10-02", summary_device_line=LONG_SUMMARY)
    books = render_screen("books", view_from_db(db, jobs_settings, "2026-10-02"), NOON)
    assert books.total_ms > 5000
    clock, adapter = Clock(), FakeAdapter()
    rotation = DeviceRotation(jobs_settings, opener(jobs_settings), adapter, clock)
    shown = []
    for _ in range(3):
        shown.append(rotation.tick())
        clock.now += timedelta(seconds=20)
    clock.now -= timedelta(seconds=20)
    assert shown == ["week", "today", "books"]

    clock.now += timedelta(milliseconds=books.total_ms - 1)
    assert rotation.tick() == HOLDING
    assert len(adapter.sent) == 3
    clock.now += timedelta(milliseconds=1)
    assert rotation.tick() == "week"


def test_the_default_dwell_is_a_few_seconds_and_books_still_pages_through(db, jobs_settings):
    longest = "".join("ab "[i % 3] for i in range(400))
    set_day(db, "2026-10-02", summary_device_line=longest)
    view = view_from_db(db, jobs_settings, "2026-10-02")
    dwell = load_settings().device.screen_seconds
    assert dwell == DeviceConfig().screen_seconds == 6
    holds = {name: hold for name, _, hold in rotation_sequence(view, NOON, dwell)}
    books = dict(rotation_clips(view, NOON))["books"]
    assert books.total_ms > 8000
    assert holds == {"week": 6000, "today": 6000, "books": books.total_ms}


def test_each_earned_small_win_gets_its_own_slot_between_today_and_books(db, jobs_settings):
    set_day(
        db,
        "2026-10-02",
        quality_workout=True,
        sleep_hours=7.5,
        book_finished_today=True,
        summary_device_line="Short.",
    )
    settings = with_device(jobs_settings, seconds=6)
    clock, adapter = Clock(), FakeAdapter()
    rotation = DeviceRotation(settings, opener(settings), adapter, clock)
    shown = []
    for _ in range(40):
        result = rotation.tick()
        if result != HOLDING:
            shown.append((result, (clock.now - NOON).total_seconds()))
        clock.now += timedelta(seconds=1)
    assert shown[:7] == [
        ("week", 0),
        ("today", 6),
        ("win-workout", 12),
        ("win-sleep", 17),
        ("win-book", 22),
        ("books", 27),
        ("week", 33),
    ]
    assert same(adapter.sent[2], sparkle_clip("workout"))
    assert same(adapter.sent[4], sparkle_clip("book"))

    db.execute(
        "UPDATE daily_metrics SET metrics_json = ? WHERE day_local = '2026-10-02'",
        (json.dumps({"quality_workout": False, "sleep_hours": 6.0, "steps": 4000}),),
    )
    db.commit()
    quiet = DeviceRotation(settings, opener(settings), FakeAdapter(), Clock())
    assert [quiet.tick() for _ in range(1)] == ["week"]
    view = view_from_db(db, settings, "2026-10-02")
    assert [name for name, _, _ in rotation_sequence(view, NOON, 6)] == ["week", "today", "books"]


def test_without_a_host_nothing_is_registered_built_or_sent(db, jobs_settings, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("no device is configured: nothing may be built or dialled")

    monkeypatch.setattr(rotation_job, "PixooAdapter", forbidden)
    monkeypatch.setattr(rotation_job, "DeviceRotation", forbidden)
    monkeypatch.setattr(jobs, "DeviceRotation", forbidden)
    monkeypatch.setattr(httpx.Client, "send", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    assert load_settings().device.pixoo_host == ""
    assert jobs_settings.device.pixoo_host == ""

    spy = FakeAdapter()
    scheduler = jobs.build_scheduler(jobs_settings, opener(jobs_settings), device=spy)
    assert {job.id for job in scheduler.get_jobs()} == jobs.CORE_JOBS
    assert ROTATION_JOB not in scheduler.job_stats
    for job in scheduler.get_jobs():
        job.func()
    assert spy.attempts == 0
    assert all(stats.failures == 0 for stats in scheduler.job_stats.values())


def test_with_a_host_the_job_is_registered_and_sends_through_the_pixoo_adapter(db, jobs_settings):
    settings = with_device(jobs_settings)
    seen: list[httpx.Request] = []
    device = PixooAdapter(HOST, fake_device(seen))
    scheduler = jobs.build_scheduler(settings, opener(settings), NOON, device)
    scheduler.start(paused=True)
    try:
        job = scheduler.get_job(ROTATION_JOB)
        assert job.trigger.interval == timedelta(seconds=20)
        assert job.max_instances == 1 and job.coalesce
        assert job.next_run_time == NOON
        assert job.misfire_grace_time == 20
        before = datetime.now(UTC)
        job.func()
        booked = scheduler.get_job(ROTATION_JOB)
        wait = booked.next_run_time - before
        assert timedelta(seconds=19) < wait < timedelta(seconds=25), "one 20 s dwell for a still"
    finally:
        scheduler.shutdown(wait=False)
    assert scheduler.job_stats[ROTATION_JOB] == jobs.JobStats(runs=1)
    assert {str(request.url) for request in seen} == {f"http://{HOST}/post"}
    commands = [json.loads(request.content)["Command"] for request in seen]
    assert commands[0] == "Draw/GetHttpGifId"
    assert commands[1:] == ["Draw/SendHttpGif"] * (len(seen) - 1) and len(seen) >= 2


def test_the_real_adapter_is_built_for_the_host_with_short_timeouts(jobs_settings):
    adapter = device_adapter(with_device(jobs_settings, seconds=20))
    assert isinstance(adapter, PixooAdapter)
    assert adapter.host == HOST
    assert adapter._client.timeout == httpx.Timeout(2.0)
    assert adapter._send_budget_s == 10
    short = device_adapter(with_device(jobs_settings, seconds=rotation_job.MIN_SCREEN_SECONDS))
    assert short._send_budget_s == rotation_job.MIN_SEND_BUDGET_S == 7.5


@pytest.mark.parametrize(
    ("host", "seconds"), [("8.8.8.8", 20), ("pixoo.local", 20), (HOST, 2), (HOST, 0)]
)
def test_a_bad_device_config_registers_no_job_and_stops_nothing(
    jobs_settings, caplog, host, seconds
):
    settings = with_device(jobs_settings, seconds, host)
    scheduler = jobs.build_scheduler(settings, opener(settings))
    assert {job.id for job in scheduler.get_jobs()} == jobs.CORE_JOBS
    assert f"{ROTATION_JOB} not registered" in caplog.text


def test_the_job_comes_back_after_a_restart_and_starts_from_the_first_screen(
    db, jobs_settings, monkeypatch
):
    settings = with_device(jobs_settings)
    settings = replace(settings, scheduler=replace(settings.scheduler, enabled=True))
    seen: list[dict] = []
    sent_whole = threading.Event()

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body)
        if body.get("PicOffset") == body.get("PicNum", 0) - 1:
            sent_whole.set()
        return httpx.Response(200, json={"error_code": 0, "PicId": 7})

    def mock_adapter(settings):
        return PixooAdapter(HOST, httpx.Client(transport=httpx.MockTransport(handler)))

    monkeypatch.setattr(jobs, "device_adapter", mock_adapter)

    schedulers = []
    for _ in range(2):
        seen.clear()
        sent_whole.clear()
        app = create_app(settings)
        with TestClient(app):
            running = app.state.scheduler
            assert {job.id for job in running.get_jobs()} == jobs.CORE_JOBS | {ROTATION_JOB}
            assert sent_whole.wait(timeout=5)
        schedulers.append(running)
        assert running.job_stats[ROTATION_JOB] == jobs.JobStats(runs=1)
        today = local_day(datetime.now(UTC), settings.home_tz)
        week = render_screen("week", view_from_db(db, settings, today), NOON)
        assert seen[0] == {"Command": "Draw/GetHttpGifId"}
        assert len(seen) == 1 + len(week.frames)
    assert schedulers[0] is not schedulers[1]


def test_a_week_finished_early_keeps_the_workout_win_until_the_week_ends(db, jobs_settings):
    db.execute(
        "INSERT INTO weekly_metrics VALUES ('2026-09-28', ?)",
        (json.dumps({"quality_workouts": 3, "weeks_hit_streak": 5}),),
    )
    rest_day = {"quality_workout": False, "workout_count": 0, "sleep_hours": 6.0, "steps": 4000}
    for day in ("2026-10-01", "2026-10-04", "2026-10-05"):
        set_day(db, day, **rest_day)

    def names(day: str) -> list[str]:
        return list(sequence_names(view_from_db(db, jobs_settings, day)))

    assert names("2026-10-01") == ["week", "today", "win-workout", "books"]
    assert names("2026-10-04") == ["week", "today", "win-workout", "books"], "Sunday still plays"
    assert names("2026-10-05") == ["week", "today", "books"], "Monday starts a new week"

    db.execute(
        "UPDATE weekly_metrics SET metrics_json = ? WHERE week_start_local = '2026-09-28'",
        (json.dumps({"quality_workouts": 2, "weeks_hit_streak": 5}),),
    )
    assert names("2026-10-01") == ["week", "today", "books"]


def test_a_failed_tick_waits_one_dwell_and_never_retries_every_second(db, jobs_settings):
    settings = with_device(jobs_settings, seconds=6)
    clock, adapter = Clock(), FakeAdapter(fail_on_sends=(1,))
    rotation = DeviceRotation(settings, opener(settings), adapter, clock)
    with pytest.raises(PixooError):
        rotation.tick()
    assert rotation.due() == NOON + timedelta(seconds=6)
    attempts = 0
    for _ in range(5):
        clock.now += timedelta(seconds=1)
        if rotation.tick() != HOLDING:
            attempts += 1
    assert attempts == 0


def test_holds_are_whole_plays_and_due_is_exact(db, jobs_settings):
    set_day(db, "2026-10-02", quality_workout=True, sleep_hours=6.0, summary_device_line="x" * 40)
    settings = with_device(jobs_settings, seconds=6)
    view = view_from_db(db, settings, "2026-10-02")
    slots = {name: (clip, hold) for name, clip, hold in rotation_sequence(view, NOON, 6)}
    assert list(slots) == ["week", "today", "win-workout", "books"]
    books, books_hold = slots["books"]
    assert (len(books.frames), books.total_ms, books_hold) == (2, 4000, 8000), "two full passes"
    assert slots["win-workout"][1] == 4200

    clock = Clock()
    rotation = DeviceRotation(settings, opener(settings), FakeAdapter(), clock)
    for name, offset_ms in (("week", 6000), ("today", 12000), ("win-workout", 16200)):
        assert rotation.tick() == name
        assert rotation.due() == NOON + timedelta(milliseconds=offset_ms)
        clock.now = rotation.due()


def test_a_sequence_that_shrinks_restarts_at_week_instead_of_skipping_it(db, jobs_settings):
    set_day(db, "2026-10-02", quality_workout=True, sleep_hours=7.5, book_finished_today=True)
    settings = with_device(jobs_settings, seconds=6)
    clock = Clock()
    rotation = DeviceRotation(settings, opener(settings), FakeAdapter(), clock)
    shown = []
    for _ in range(4):
        shown.append(rotation.tick())
        clock.now = rotation.due()
    assert shown == ["week", "today", "win-workout", "win-sleep"]
    db.execute(
        "UPDATE daily_metrics SET metrics_json = ? WHERE day_local = '2026-10-02'",
        (json.dumps({"quality_workout": False, "sleep_hours": 6.0, "steps": 4000}),),
    )
    db.commit()
    for _ in range(3):
        shown.append(rotation.tick())
        clock.now = rotation.due()
    assert shown[4:] == ["week", "today", "books"]


def test_every_win_belongs_to_the_day_the_today_screen_shows(db, jobs_settings):
    set_day(db, "2026-10-01", quality_workout=True, sleep_hours=7.5, book_finished_today=True)
    set_day(db, "2026-10-02", quality_workout=False, workout_count=0)
    friday = view_from_db(db, jobs_settings, "2026-10-02")
    assert friday.day_shown == "2026-10-01"
    assert list(sequence_names(friday)) == [
        "week",
        "today",
        "win-workout",
        "win-sleep",
        "win-book",
        "books",
    ]
    db.execute(
        "UPDATE daily_metrics SET metrics_json = ? WHERE day_local = '2026-10-02'",
        (json.dumps({"quality_workout": False, "workout_count": 0, "book_finished_today": True}),),
    )
    db.execute(
        "UPDATE daily_metrics SET metrics_json = ? WHERE day_local = '2026-10-01'",
        (json.dumps({"quality_workout": False, "sleep_hours": 6.0, "steps": 4000}),),
    )
    db.commit()
    later = view_from_db(db, jobs_settings, "2026-10-02")
    assert later.day_shown == "2026-10-01"
    assert list(sequence_names(later)) == ["week", "today", "books"], "today's book waits a day"
