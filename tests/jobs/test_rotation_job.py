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

from app.config import REPO_ROOT, DeviceConfig, load_settings
from app.db import open_db
from app.jobs import rotation as rotation_job
from app.jobs import scheduler as jobs
from app.jobs.rotation import (
    HOLDING,
    ROTATION_JOB,
    SEND_ERRORS,
    DeviceRotation,
    device_adapter,
)
from app.main import create_app
from app.render.adapters.pixoo import PixooAdapter, PixooError
from app.render.celebrate import STEP_MS, party_clip, sparkle_clip
from app.render.frame import Clip, still
from app.render.rotation import (
    ROTATION_ORDER,
    device_parts,
    render_screen,
    rotation_clips,
    rotation_sequence,
    sequence_names,
)
from app.render.view import fixture_feature
from app.render.view_db import view_from_db
from app.timeutil import from_utc_iso, local_day

HOST = "192.168.1.50"
NOON = datetime(2026, 10, 2, 16, 0, tzinfo=UTC)
PLAIN = ["today", "city", "week", "month", "books"]
DAYS_FILE = REPO_ROOT / "fixtures" / "days" / "any.json"


def sample_feature(month: str):
    return fixture_feature(DAYS_FILE, "sample-2026-10", month)


LONG_SUMMARY = (
    "Three good sessions this week and a full night of sleep; the steady work is adding up, "
    "so today can be easy."
)


class Clock:
    def __init__(self, now: datetime = NOON) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


class FakeAdapter:
    def __init__(self, fail_on_sends: tuple[int, ...] = ()) -> None:
        self.sent: list[Clip] = []
        self.levels: list[int] = []
        self.attempts = 0
        self._fail_on_sends = fail_on_sends

    def send(self, clip: Clip) -> None:
        self.attempts += 1
        if self.attempts in self._fail_on_sends:
            raise PixooError("Draw/GetHttpGifId: timed out")
        self.sent.append(clip)

    def set_brightness(self, percent: int) -> None:
        self.levels.append(percent)


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
        return httpx.Response(200, json={"ReturnCode": 0})

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_ticks_walk_the_rotation_in_order_and_wrap(db, jobs_settings):
    clock, adapter = Clock(), FakeAdapter()
    rotation = DeviceRotation(jobs_settings, opener(jobs_settings), adapter, clock)
    shown = []
    for _ in range(11):
        shown.append(rotation.tick())
        clock.now += timedelta(seconds=20)
    assert shown == [*ROTATION_ORDER, *ROTATION_ORDER, ROTATION_ORDER[0]]
    assert shown[:5] == ["today", "city", "week", "month", "books"], "day, city, week, month, year"

    view = view_from_db(db, jobs_settings, "2026-10-02")
    assert [name for name, _ in rotation_clips(view, NOON)] == shown[:5]
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
    before = datetime(2026, 10, 2, 3, 59, 59, tzinfo=UTC)
    after = datetime(2026, 10, 2, 4, 0, 0, tzinfo=UTC)
    expected = {
        day: render_screen("today", view_from_db(db, jobs_settings, day), NOON)
        for day in ("2026-10-01", "2026-10-02")
    }
    assert not same(expected["2026-10-01"], expected["2026-10-02"])

    for moment, day in ((before, "2026-10-01"), (after, "2026-10-02")):
        clock, adapter = Clock(moment), FakeAdapter()
        rotation = DeviceRotation(jobs_settings, opener(jobs_settings), adapter, clock)
        assert rotation.tick() == "today"
        assert same(adapter.sent[0], expected[day])
        clock.now += timedelta(seconds=20)
        assert rotation.tick() == "city"


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
    assert same(adapter.sent[0], render_screen("week", view, NOON))
    assert stats[ROTATION_JOB] == jobs.JobStats(runs=3, failures=2, last_error=None)
    clock.now += timedelta(seconds=20)
    assert rotation.tick() == "month"


@pytest.mark.parametrize("broken", ["today", "city", "month"])
def test_a_screen_that_cannot_render_does_not_stop_the_others(
    db, jobs_settings, monkeypatch, broken
):
    def broken_screen(name, view, now):
        if name == broken:
            raise ZeroDivisionError("layout bug")
        return render_screen(name, view, now)

    monkeypatch.setattr(rotation_job, "render_screen", broken_screen)
    adapter, clock = FakeAdapter(), Clock()
    stats: dict[str, jobs.JobStats] = {}
    rotation = DeviceRotation(jobs_settings, opener(jobs_settings), adapter, clock)
    run = jobs.guarded(ROTATION_JOB, rotation.tick, stats)
    for _ in range(10):
        run()
        clock.now += timedelta(seconds=20)
    assert (stats[ROTATION_JOB].runs, stats[ROTATION_JOB].failures) == (10, 2)
    assert adapter.attempts == len(adapter.sent) == 8
    view = view_from_db(db, jobs_settings, "2026-10-02")
    others = [name for name in ROTATION_ORDER if name != broken]
    for index, name in enumerate(others * 2):
        assert (
            adapter.sent[index].poster.tobytes() == render_screen(name, view, NOON).poster.tobytes()
        )


def test_a_month_renderer_that_raises_costs_only_the_month_slot(db, jobs_settings, monkeypatch):
    from app.render import rotation as render_rotation

    def boom(view, now):
        raise RuntimeError("plate bug")

    monkeypatch.setattr(render_rotation, "render_month", boom)
    adapter, clock = FakeAdapter(), Clock()
    rotation = DeviceRotation(jobs_settings, opener(jobs_settings), adapter, clock)
    shown = []
    for _ in range(6):
        try:
            shown.append(rotation.tick())
        except RuntimeError:
            shown.append("failed")
        clock.now = rotation.due()
    assert shown == ["today", "city", "week", "failed", "books", "today"]
    assert len(adapter.sent) == 5


def test_month_goes_as_two_stills_the_plate_then_the_calendar_note_or_not(
    db, jobs_settings, monkeypatch
):
    feature = sample_feature("2026-10")
    monkeypatch.setattr("app.month.store.load_feature", lambda conn, month: feature)
    assert feature.plate("2026-10-01").note and not feature.plate("2026-10-02").note
    for day, noon in (("2026-10-01", NOON - timedelta(days=1)), ("2026-10-02", NOON)):
        view = view_from_db(db, jobs_settings, day)
        month = render_screen("month", view, noon)
        clock, adapter = Clock(noon), FakeAdapter()
        rotation = DeviceRotation(jobs_settings, opener(jobs_settings), adapter, clock)
        for name in ("today", "city", "week"):
            assert rotation.tick() == name
            clock.advance(20)
        assert month.durations_ms == (6000, 6000), "the plate, then the calendar"
        pages = ((0, 6), (1, 6))
        for page, seconds in pages:
            assert rotation.tick() == "month"
            sent = adapter.sent[-1]
            assert len(sent.frames) == 1, "a page is a still, so the panel has nothing to loop"
            assert sent.frames[0].tobytes() == month.frames[page].tobytes()
            assert rotation.due() == clock.now + timedelta(seconds=seconds)
            clock.advance(seconds - 0.001)
            assert rotation.tick() == HOLDING
            clock.advance(0.001)
        assert month.frames[0].tobytes() != month.frames[1].tobytes()
        assert rotation.tick() == "books"
        assert len(adapter.sent) == 3 + len(month.frames) + 1


def test_city_goes_page_by_page_as_stills_in_order_and_a_failed_page_costs_the_rest(
    db, jobs_settings, monkeypatch
):
    import sys
    from types import SimpleNamespace

    from app.city.model import CityStatus, LineStatus, Weather

    fetched = "2026-10-02T15:55:00Z"
    lines = (
        LineStatus("N", "subway"),
        LineStatus("W", "subway", "suspended", "No W trains.", True, 1),
        LineStatus("M", "subway", "delays", "M trains are delayed.", True, 1),
        LineStatus("Q69", "bus"),
    )
    city = CityStatus("2026-10-02", Weather(57.2, 55.0, 3, 62.4, 50.6, 70, (), (), fetched), lines)
    city = replace(city, transit_fetched_at_utc=fetched)
    store = SimpleNamespace(load_status=lambda conn, day: replace(city, day_local=day))
    monkeypatch.setitem(sys.modules, "app.city.store", store)
    expected = render_screen("city", view_from_db(db, jobs_settings, "2026-10-02"), NOON)
    assert expected.durations_ms == (6000, 6000, 5000, 5000)

    clock, adapter = Clock(), FakeAdapter()
    rotation = DeviceRotation(jobs_settings, opener(jobs_settings), adapter, clock)
    assert rotation.tick() == "today"
    clock.advance(20)
    for page, seconds in enumerate((6, 6, 5, 5)):
        assert rotation.tick() == "city"
        sent = adapter.sent[-1]
        assert len(sent.frames) == 1, "a page is a still, so the panel has nothing to loop"
        assert sent.frames[0].tobytes() == expected.frames[page].tobytes()
        assert rotation.due() == clock.now + timedelta(seconds=seconds)
        clock.advance(seconds - 0.001)
        assert rotation.tick() == HOLDING
        clock.advance(0.001)
    assert rotation.tick() == "week"
    assert len(adapter.sent) == 6

    clock, adapter = Clock(), FakeAdapter(fail_on_sends=(3,))
    rotation = DeviceRotation(jobs_settings, opener(jobs_settings), adapter, clock)
    for name in ("today", "city"):
        assert rotation.tick() == name
        clock.now = rotation.due()
    with pytest.raises(PixooError):
        rotation.tick()
    clock.now = rotation.due()
    assert rotation.tick() == "week", "the lost pages are dropped; the rotation moves on"


def test_a_finished_week_ends_the_cycle_with_one_whole_party(db, jobs_settings):
    db.execute(
        "INSERT INTO weekly_metrics VALUES ('2026-09-28', ?)",
        (json.dumps({"quality_workouts": 3, "weeks_hit_streak": 5}),),
    )
    set_day(db, "2026-10-02", quality_workout=True, sleep_hours=6.0, steps=4000)
    settings = with_device(jobs_settings, seconds=6)
    clock, adapter = Clock(), FakeAdapter()
    rotation = DeviceRotation(settings, opener(settings), adapter, clock)
    shown = []
    for _ in range(17):
        shown.append(rotation.tick())
        last_due = rotation.due() - clock.now
        clock.now = rotation.due()
    assert shown == [
        *["today", "city", "week", "month", "books"],
        *["win-workout"] * 5,
        *["party"] * 6,
        "today",
    ]
    party = party_clip(3, 3)
    assert len(party.frames) == 6
    for step, sent in zip(party.frames, adapter.sent[10:16], strict=True):
        assert same(sent, still(step, STEP_MS)), "the party goes as six stills of STEP_MS"
    view = view_from_db(db, settings, "2026-10-02")
    holds = {name: hold for name, _, hold in rotation_sequence(view, NOON, 6)}
    assert holds["party"] == party.total_ms == 6 * STEP_MS, "one pass, not the dwell"
    assert last_due == timedelta(seconds=6), "and Today follows it"


def test_books_goes_page_by_page_as_stills_each_shown_once(db, jobs_settings):
    set_day(db, "2026-10-02", summary_device_line=LONG_SUMMARY)
    books = render_screen("books", view_from_db(db, jobs_settings, "2026-10-02"), NOON)
    pages = len(books.frames)
    assert pages > 2 and set(books.durations_ms) == {2000}
    clock, adapter = Clock(), FakeAdapter()
    rotation = DeviceRotation(jobs_settings, opener(jobs_settings), adapter, clock)
    for name in ("today", "city", "week", "month"):
        assert rotation.tick() == name
        clock.advance(20)
    for page in range(pages):
        assert rotation.tick() == "books"
        sent = adapter.sent[-1]
        assert len(sent.frames) == 1, "a page is a still, so the panel has nothing to loop"
        assert sent.frames[0].tobytes() == books.frames[page].tobytes()
        clock.advance(1.999)
        assert rotation.tick() == HOLDING
        clock.advance(0.001)
    assert rotation.tick() == "today"
    assert len(adapter.sent) == 4 + pages + 1


def test_a_page_that_fails_drops_the_rest_and_the_rotation_moves_on(db, jobs_settings):
    set_day(db, "2026-10-02", summary_device_line=LONG_SUMMARY)
    clock, adapter = Clock(), FakeAdapter(fail_on_sends=(6,))
    rotation = DeviceRotation(jobs_settings, opener(jobs_settings), adapter, clock)
    for name in ("today", "city", "week", "month", "books"):
        assert rotation.tick() == name
        clock.now = rotation.due()
    with pytest.raises(PixooError):
        rotation.tick()
    clock.now = rotation.due()
    assert rotation.tick() == "today"


def test_a_fast_animation_still_goes_whole_and_a_celebration_goes_as_steps(db, jobs_settings):
    sparkle = sparkle_clip("workout")
    assert [len(part.frames) for part in device_parts(sparkle)] == [1] * 5
    assert [len(part.frames) for part in device_parts(party_clip(3, 3))] == [1] * 6
    week = render_screen("week", view_from_db(db, jobs_settings, "2026-10-02"), NOON)
    assert device_parts(week) == [week]


def test_the_default_dwell_is_a_few_seconds_and_books_still_pages_through(db, jobs_settings):
    longest = "".join("ab "[i % 3] for i in range(400))
    set_day(db, "2026-10-02", summary_device_line=longest)
    view = view_from_db(db, jobs_settings, "2026-10-02")
    dwell = load_settings().device.screen_seconds
    assert dwell == DeviceConfig().screen_seconds == 6
    holds = {name: hold for name, _, hold in rotation_sequence(view, NOON, dwell)}
    books = dict(rotation_clips(view, NOON))["books"]
    assert books.total_ms > 8000
    assert holds == {
        "today": 6000,
        "city": 6000,
        "week": 6000,
        "month": 6000,
        "books": books.total_ms,
    }


def test_each_earned_small_win_gets_its_own_slot_after_books(db, jobs_settings):
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
    for _ in range(48):
        result = rotation.tick()
        if result != HOLDING and (not shown or shown[-1][0] != result):
            shown.append((result, (clock.now - NOON).total_seconds()))
        clock.now += timedelta(seconds=1)
    assert shown[:9] == [
        ("today", 0),
        ("city", 6),
        ("week", 12),
        ("month", 18),
        ("books", 24),
        ("win-workout", 30),
        ("win-sleep", 35),
        ("win-book", 40),
        ("today", 45),
    ], "each sparkle is five steps of STEP_MS, sent one still at a time"
    assert same(adapter.sent[5], still(sparkle_clip("workout").frames[0], STEP_MS))
    assert same(adapter.sent[15], still(sparkle_clip("book").frames[0], STEP_MS))

    db.execute(
        "UPDATE daily_metrics SET metrics_json = ? WHERE day_local = '2026-10-02'",
        (json.dumps({"quality_workout": False, "sleep_hours": 6.0, "steps": 4000}),),
    )
    db.commit()
    quiet = DeviceRotation(settings, opener(settings), FakeAdapter(), Clock())
    assert [quiet.tick() for _ in range(1)] == ["today"]
    view = view_from_db(db, settings, "2026-10-02")
    assert [name for name, _, _ in rotation_sequence(view, NOON, 6)] == [
        "today",
        "city",
        "week",
        "month",
        "books",
    ]


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
        assert job.trigger.interval == timedelta(seconds=rotation_job.ROTATION_FALLBACK_S)
        assert job.max_instances == 1 and job.coalesce
        assert job.next_run_time == NOON
        assert job.misfire_grace_time == 24 * 3600
        before = datetime.now(UTC)
        job.func()
        booked = scheduler.get_job(ROTATION_JOB)
        wait = booked.next_run_time - before
        assert timedelta(seconds=19) < wait < timedelta(seconds=25), "one 20 s dwell for a still"
    finally:
        scheduler.shutdown(wait=False)
    assert scheduler.job_stats[ROTATION_JOB] == jobs.JobStats(runs=1)
    assert {str(request.url) for request in seen} == {f"http://{HOST}:9000/divoom_api"}
    commands = [json.loads(request.content)["Command"] for request in seen]
    assert commands[:2] == ["Channel/SetBrightness", "Draw/ResetHttpGifId"]
    assert commands[2:] == ["Draw/SendHttpGif"] * (len(seen) - 2) and len(seen) >= 3
    assert "Device/PlayTFGif" not in commands, "fetch_clips is off by default"


def test_the_real_adapter_is_built_for_the_host_with_short_timeouts(jobs_settings):
    adapter = device_adapter(with_device(jobs_settings, seconds=20))
    assert isinstance(adapter, PixooAdapter)
    assert adapter.host == HOST
    assert adapter._client.timeout == httpx.Timeout(5.0)
    assert adapter._frame_budget_s == 4.0


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
        return httpx.Response(200, json={"ReturnCode": 0})

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
        first = render_screen("today", view_from_db(db, settings, today), NOON)
        assert sequence_names(view_from_db(db, settings, today))[0] == "today"
        assert seen[0]["Command"] == "Channel/SetBrightness"
        assert seen[1] == {"Command": "Draw/ResetHttpGifId"}
        assert len(seen) == 2 + len(first.frames) == 3
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

    done = ["today", "city", "week", "month", "books", "win-workout", "party"]
    assert names("2026-10-01") == done
    assert names("2026-10-04") == done, "Sunday still plays"
    assert names("2026-10-05") == PLAIN, "Monday starts a new week"

    db.execute(
        "UPDATE weekly_metrics SET metrics_json = ? WHERE week_start_local = '2026-09-28'",
        (json.dumps({"quality_workouts": 2, "weeks_hit_streak": 5}),),
    )
    assert names("2026-10-01") == PLAIN, "two of three: no workout win kept, no party"


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
    assert list(slots) == ["today", "city", "week", "month", "books", "win-workout"]
    books, books_hold = slots["books"]
    assert (len(books.frames), books.total_ms, books_hold) == (2, 4000, 4000), "one pass, never two"
    assert slots["win-workout"][1] == 5 * STEP_MS == 1500, "one pass of its steps"

    clock = Clock()
    rotation = DeviceRotation(settings, opener(settings), FakeAdapter(), clock)
    steps = (
        ("today", 6000),
        ("city", 12000),
        ("week", 18000),
        ("month", 24000),
        ("books", 26000),
        ("books", 28000),
        ("win-workout", 28300),
        ("win-workout", 28600),
        ("win-workout", 28900),
        ("win-workout", 29200),
        ("win-workout", 29500),
    )
    for name, offset_ms in steps:
        assert rotation.tick() == name
        assert rotation.due() == NOON + timedelta(milliseconds=offset_ms)
        clock.now = rotation.due()


def test_a_sequence_that_shrinks_restarts_at_today_instead_of_skipping_it(db, jobs_settings):
    set_day(db, "2026-10-02", quality_workout=True, sleep_hours=7.5, book_finished_today=True)
    settings = with_device(jobs_settings, seconds=6)
    clock = Clock()
    rotation = DeviceRotation(settings, opener(settings), FakeAdapter(), clock)
    shown = []
    for _ in range(6):
        shown.append(rotation.tick())
        clock.now = rotation.due()
    assert shown == ["today", "city", "week", "month", "books", "win-workout"]
    db.execute(
        "UPDATE daily_metrics SET metrics_json = ? WHERE day_local = '2026-10-02'",
        (json.dumps({"quality_workout": False, "sleep_hours": 6.0, "steps": 4000}),),
    )
    db.commit()
    for _ in range(9):
        shown.append(rotation.tick())
        clock.now = rotation.due()
    assert shown[6:10] == ["win-workout"] * 4, "the celebration under way finishes its steps"
    assert shown[10:] == PLAIN


def test_every_win_belongs_to_the_day_the_today_screen_shows(db, jobs_settings):
    set_day(db, "2026-10-01", quality_workout=True, sleep_hours=7.5, book_finished_today=True)
    set_day(db, "2026-10-02", quality_workout=False, workout_count=0)
    friday = view_from_db(db, jobs_settings, "2026-10-02")
    assert friday.day_shown == "2026-10-01"
    assert list(sequence_names(friday)) == [*PLAIN, "win-workout", "win-sleep", "win-book"]
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
    assert list(sequence_names(later)) == PLAIN, "today's book waits a day"


def test_a_device_that_stays_off_is_logged_once_not_every_tick(caplog):
    stats: dict[str, jobs.JobStats] = {}
    errors = iter(["timed out", "timed out", "timed out", "refused"])

    def tick() -> None:
        raise PixooError(next(errors))

    run = jobs.guarded(ROTATION_JOB, tick, stats, quiet=SEND_ERRORS)
    for _ in range(4):
        run()
    assert stats[ROTATION_JOB].failures == 4
    lines = [r for r in caplog.records if f"job {ROTATION_JOB} failed" in r.getMessage()]
    assert [("timed out" in r.getMessage(), r.exc_info) for r in lines] == [
        (True, None),
        (False, None),
    ]

    def boom() -> None:
        raise PixooError("boom")

    jobs.guarded("other", boom, {})()
    assert caplog.records[-1].exc_info is not None


def test_interval_jobs_run_once_after_a_sleep_instead_of_logging_a_miss(db, jobs_settings):
    scheduler = jobs.build_scheduler(with_device(jobs_settings), opener(jobs_settings))
    grace = {job.id: job.misfire_grace_time for job in scheduler.get_jobs()}
    assert all(seconds is not None and seconds >= 6 for seconds in grace.values()), grace


def test_the_panel_is_dimmed_from_ten_at_night_until_half_past_five(db, jobs_settings):
    dim, day = jobs_settings.device.night_brightness, jobs_settings.device.brightness
    assert (day, dim) == (25, 1), "a quarter-bright day level; the night is far dimmer"
    night = from_utc_iso("2026-10-05T02:00:00Z")  # 22:00 New York
    cases = {
        "2026-10-05T01:59:00Z": day,  # 21:59
        "2026-10-05T02:00:00Z": dim,  # 22:00
        "2026-10-05T07:00:00Z": dim,  # 03:00
        "2026-10-05T09:29:00Z": dim,  # 05:29
        "2026-10-05T09:30:00Z": day,  # 05:30
        "2026-10-05T16:00:00Z": day,  # noon
    }
    for stamp, level in cases.items():
        assert rotation_job.brightness_at(from_utc_iso(stamp), jobs_settings) == level, stamp

    clock, adapter = Clock(), FakeAdapter()
    clock.now = night - timedelta(minutes=1)
    rotation = DeviceRotation(jobs_settings, opener(jobs_settings), adapter, clock)
    for _ in range(4):
        rotation.tick()
        clock.now += timedelta(seconds=30)
    assert adapter.levels == [day, dim], "set once at start, once when the night begins"
    clock.now = from_utc_iso("2026-10-05T09:30:00Z")
    rotation.tick()
    assert adapter.levels == [day, dim, day]


def test_a_brightness_command_that_fails_is_sent_again_next_tick(db, jobs_settings):
    class Deaf(FakeAdapter):
        def set_brightness(self, percent: int) -> None:
            super().set_brightness(percent)
            if len(self.levels) == 1:
                raise PixooError("Channel/SetBrightness: timed out")

    clock, adapter = Clock(), Deaf()
    rotation = DeviceRotation(jobs_settings, opener(jobs_settings), adapter, clock)
    with pytest.raises(PixooError):
        rotation.tick()
    clock.now = rotation.due()
    rotation.tick()
    assert adapter.levels == [25, 25] and len(adapter.sent) == 1


def test_every_send_is_recorded_with_its_page_time_and_hold(db, jobs_settings):
    set_day(db, "2026-10-02", summary_device_line=LONG_SUMMARY)
    clock, adapter = Clock(), FakeAdapter(fail_on_sends=(2,))
    rotation = DeviceRotation(jobs_settings, opener(jobs_settings), adapter, clock)
    names = sequence_names(view_from_db(db, jobs_settings, "2026-10-02"))
    for _ in range(40):
        try:
            rotation.tick()
        except PixooError:
            pass
        clock.now = rotation.due()
    sends = list(rotation.sends)
    assert sends[0].name == names[0] and sends[0].error is None and sends[0].hold_ms > 0
    assert sends[1].error == "PixooError: Draw/GetHttpGifId: timed out"
    books = [s for s in sends if s.name == "books"]
    pages = books[0].pages
    assert pages > 1 and [s.page for s in books[:pages]] == list(range(1, pages + 1))
    assert all(s.frames == 1 and s.hold_ms == 2000 for s in books)
    assert all(s.seconds >= 0 for s in sends) and len(sends) <= rotation_job.SEND_LOG_SIZE


def test_the_send_log_is_served_as_json(jobs_settings, db):
    settings = with_device(jobs_settings)
    with TestClient(create_app(settings)) as client:
        body = client.get("/display/sends.json").json()
    assert body["sends"] == [] and isinstance(body["jobs"], dict)


class FetchingAdapter(FakeAdapter):
    def __init__(self, knows_fetch: bool = True) -> None:
        super().__init__()
        self.urls: list[str] = []
        self._knows = knows_fetch

    def play_url(self, url: str) -> bool:
        self.urls.append(url)
        return self._knows


def test_with_a_publisher_every_clip_is_fetched_and_nothing_is_uploaded(db, jobs_settings):
    set_day(db, "2026-10-02", quality_workout=True, sleep_hours=8.0)
    clock, adapter = Clock(), FetchingAdapter()
    published = []

    def publish(clip):
        published.append(len(clip.frames))
        return f"http://192.168.1.171:8080/pixoo/clip/{len(published)}.gif"

    rotation = DeviceRotation(jobs_settings, opener(jobs_settings), adapter, clock, publish)
    names = []
    for _ in range(12):
        names.append(rotation.tick())
        clock.now = rotation.due()
    assert "win-workout" in names and "win-sleep" in names and "today" in names
    assert adapter.urls == [
        f"http://192.168.1.171:8080/pixoo/clip/{n}.gif" for n in range(1, len(published) + 1)
    ]
    assert published == [1] * 12, "stills and celebration steps alike, one frame each"
    assert adapter.sent == [], "a still uploaded between two fetched clips flashes the heart"
    assert {s.how for s in rotation.sends} == {"fetched"} and len(rotation.sends) == 12


def test_a_panel_that_does_not_know_the_fetch_command_gets_uploads_and_is_not_asked_again(
    db, jobs_settings
):
    set_day(db, "2026-10-02", quality_workout=True)
    clock, adapter = Clock(), FetchingAdapter(knows_fetch=False)
    rotation = DeviceRotation(jobs_settings, opener(jobs_settings), adapter, clock, lambda c: "u")
    for _ in range(14):
        rotation.tick()
        clock.now = rotation.due()
    assert adapter.urls == ["u"], "asked once"
    uploaded = [s for s in rotation.sends if s.name == "win-workout"]
    assert len(uploaded) >= 2 and all(s.how == "uploaded" for s in uploaded)


def test_without_a_publisher_animations_are_uploaded(db, jobs_settings):
    set_day(db, "2026-10-02", quality_workout=True)
    clock, adapter = Clock(), FetchingAdapter()
    rotation = DeviceRotation(jobs_settings, opener(jobs_settings), adapter, clock)
    for _ in range(11):
        rotation.tick()
        clock.now = rotation.due()
    steps = [c for c in adapter.sent if c.durations_ms == (STEP_MS,)]
    assert adapter.urls == [] and len(steps) == 5, "the sparkle's five steps, uploaded"


def test_the_scheduler_wires_a_publisher_only_when_fetch_clips_is_on(db, jobs_settings):
    on = replace(jobs_settings, device=replace(with_device(jobs_settings).device, fetch_clips=True))
    off = with_device(jobs_settings)
    for settings, expected in ((on, True), (off, False)):
        scheduler = jobs.build_scheduler(settings, opener(settings), NOON, FakeAdapter())
        assert (scheduler.rotation._clip_url is not None) is expected
