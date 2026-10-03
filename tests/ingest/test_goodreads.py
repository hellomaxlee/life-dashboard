"""The Goodreads poller: parse every feed variant, store under the read-at / date-added rule,
archive raw first, change nothing on a re-poll, refuse any host but Goodreads."""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest

from app.config import REPO_ROOT, BackupConfig, ClaudeUsageConfig, Settings
from app.db import open_db
from app.ingest import goodreads, health
from app.ingest.goodreads import (
    Book,
    FeedError,
    PollResult,
    fetch_feed,
    parse_feed,
    poll_goodreads,
    store_books,
)
from app.jobs import scheduler as jobs
from tests.conftest import count
from tools import replay, sync
from tools.replay import checksum, snapshot

FIXTURES = REPO_ROOT / "fixtures" / "goodreads"
FEED_URL = "https://www.goodreads.com/review/list_rss/1?key=FIXTURE&shelf=read"
NY = ZoneInfo("America/New_York")

EXPECTED_BOOKS = [
    Book(
        "90000001",
        "The Lighthouse at Dusk",
        "Ada Marsh",
        "2026-02-17T05:00:00Z",
        "2026-02-18T03:52:40Z",
    ),
    Book(
        "90000002",
        "Why We're Restless",
        "Ezra O'Neill",
        "2023-11-03T04:00:00Z",
        "2023-11-04T05:17:37Z",
    ),
    Book(
        "90000003",
        "Salt & Stone: A Field Guide to the Coast (Field Guides, #2)",
        "Nadia Qureshi-Ławski",
        None,
        "2022-08-08T12:34:44Z",
    ),
    Book(
        "90000004",
        'The Handmaid’s Garden (The Gardener"s Trilogy, #1)',
        "Margaret & Roland Atwell",
        None,
        "2024-01-17T17:15:37Z",
    ),
    Book(
        "90000005",
        "Midnight on the Last Day of the Year",
        "Tomás Ferreira",
        "2025-12-31T05:00:00Z",
        "2026-01-01T05:30:00Z",
    ),
    Book("90000006", "Garbled Dates", "Anonymous", None, None),
    Book("90000007", "Nested Id Only", None, "2026-03-08T05:00:00Z", "2026-03-08T10:30:00Z"),
]
EXPECTED_ROWS = {
    "90000001": ("2026-02-17T05:00:00Z", "2026-02-18T03:52:40Z", 0),
    "90000002": ("2023-11-03T04:00:00Z", "2023-11-04T05:17:37Z", 0),
    "90000003": ("2022-08-08T12:34:44Z", "2022-08-08T12:34:44Z", 1),
    "90000004": ("2024-01-17T17:15:37Z", "2024-01-17T17:15:37Z", 1),
    "90000005": ("2025-12-31T05:00:00Z", "2026-01-01T05:30:00Z", 0),
    "90000006": (None, None, 0),
    "90000007": ("2026-03-08T05:00:00Z", "2026-03-08T10:30:00Z", 0),
}


def feed(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def rows(conn: sqlite3.Connection) -> dict[str, tuple[str | None, str | None, int]]:
    found = conn.execute("SELECT id, read_at, date_added, date_inferred FROM books").fetchall()
    return {r["id"]: (r["read_at"], r["date_added"], r["date_inferred"]) for r in found}


class Shelf:
    """A fake Goodreads: serves `body` and records every request it saw."""

    def __init__(self, body: bytes, status: int = 200, headers: dict[str, str] | None = None):
        self.body, self.status, self.headers = body, status, headers or {}
        self.requests: list[httpx.Request] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(self.status, content=self.body, headers=self.headers)

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handle))


@pytest.fixture
def gr_settings(settings: Settings) -> Settings:
    return replace(settings, goodreads_rss_url=FEED_URL)


@pytest.fixture
def jobs_settings(settings: Settings, tmp_path: Path) -> Settings:
    """As tests/jobs/conftest.py builds it: usage file and backup dir under tmp_path, no URL."""
    return replace(
        settings,
        claude_usage=ClaudeUsageConfig(tmp_path / "claude_usage.json", 24),
        backup=BackupConfig(tmp_path / "backups", "03:15", 3),
    )


def poll(conn: sqlite3.Connection, gr_settings: Settings, name: str) -> PollResult:
    shelf = Shelf(feed(name))
    with shelf.client() as client:
        return poll_goodreads(conn, gr_settings, client)


def test_parse_every_variant():
    assert parse_feed(feed("shelf_variants.xml")) == EXPECTED_BOOKS


def test_parse_skips_unkeyed_and_untitled_items_and_logs_bad_dates(caplog):
    books = parse_feed(feed("shelf_variants.xml"))
    assert {b.id for b in books} == {f"9000000{n}" for n in range(1, 8)}
    assert "skipped a feed item with no book id" in caplog.text
    assert "skipped a feed item with no title" in caplog.text
    assert "book 90000006: user_read_at 'Thu, 30 Feb 2025 00:00:00 +0000'" in caplog.text
    assert "book 90000006: user_date_added 'sometime in June'" in caplog.text


@pytest.mark.parametrize(
    ("text", "read_at", "instant"),
    [
        ("Tue, 17 Feb 2026 00:00:00 +0000", "2026-02-17T05:00:00Z", "2026-02-17T00:00:00Z"),
        ("Fri, 3 Nov 2023 00:00:00 +0000", "2023-11-03T04:00:00Z", "2023-11-03T00:00:00Z"),
        ("Fri, 03 Nov 2023 22:17:37 -0700", "2023-11-03T04:00:00Z", "2023-11-04T05:17:37Z"),
        ("17 Feb 2026 19:52:40 -0800", "2026-02-17T05:00:00Z", "2026-02-18T03:52:40Z"),
        ("Tue, 17 Feb 2026 19:52 GMT", "2026-02-17T05:00:00Z", "2026-02-17T19:52:00Z"),
        ("Tue, 17 Feb 2026 19:52:40 -0000", "2026-02-17T05:00:00Z", "2026-02-17T19:52:40Z"),
        ("2026-02-17", "2026-02-17T05:00:00Z", "2026-02-17T00:00:00Z"),
        ("2026-02-17T00:00:00Z", "2026-02-17T05:00:00Z", "2026-02-17T00:00:00Z"),
        ("2026-07-04T19:52:40-04:00", "2026-07-04T04:00:00Z", "2026-07-04T23:52:40Z"),
        ("", None, None),
        ("   ", None, None),
        ("not a date", None, None),
        ("17/02/2026", None, None),
        ("Tue, 30 Feb 2026 00:00:00 +0000", None, None),
    ],
)
def test_accepted_date_formats_and_null_for_the_rest(text, read_at, instant):
    assert goodreads.read_date_utc(text, "America/New_York") == read_at
    assert goodreads.instant_utc(text) == instant


def test_read_at_is_the_chosen_day_and_fallback_marks_date_inferred(db):
    store_books(db, parse_feed(feed("shelf_variants.xml")), fallback_to_date_added=True)
    assert rows(db) == EXPECTED_ROWS
    lighthouse = db.execute("SELECT * FROM books WHERE id = '90000001'").fetchone()
    assert lighthouse["read_at"] != lighthouse["date_added"]
    assert datetime.strptime(lighthouse["read_at"], "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=UTC
    ).astimezone(NY) == datetime(2026, 2, 17, 0, 0, tzinfo=NY)
    restless = db.execute("SELECT title, author FROM books WHERE id = '90000002'").fetchone()
    assert tuple(restless) == ("Why We're Restless", "Ezra O'Neill")


def test_fallback_off_stores_null_read_at(db):
    store_books(db, parse_feed(feed("shelf_variants.xml")), fallback_to_date_added=False)
    stored = rows(db)
    assert stored["90000003"] == (None, "2022-08-08T12:34:44Z", 0)
    assert stored["90000004"] == (None, "2024-01-17T17:15:37Z", 0)
    assert stored["90000001"] == EXPECTED_ROWS["90000001"]
    assert sum(inferred for _, _, inferred in stored.values()) == 0


def test_poll_archives_raw_first_and_an_identical_repoll_changes_nothing(db, gr_settings):
    first = poll(db, gr_settings, "shelf_variants.xml")
    assert first == PollResult("ok", 1, 7)
    row = db.execute("SELECT * FROM raw_archive").fetchone()
    assert (row["source"], row["parsed_ok"], row["error"]) == ("goodreads", 1, None)
    assert row["path"].startswith("goodreads/") and row["path"].endswith(".xml")
    archived = gr_settings.storage.raw_dir / row["path"]
    assert archived.read_bytes() == feed("shelf_variants.xml")
    assert rows(db) == EXPECTED_ROWS
    before = checksum(snapshot(db))

    second = poll(db, gr_settings, "shelf_variants.xml")

    assert second == PollResult("duplicate", 1)
    assert count(db, "raw_archive") == 1
    assert checksum(snapshot(db)) == before


def test_rebuilt_feed_adds_a_raw_file_but_changes_no_data_table(db, gr_settings):
    poll(db, gr_settings, "shelf_variants.xml")
    before = checksum(snapshot(db))
    assert feed("shelf_variants_rebuilt.xml") != feed("shelf_variants.xml")

    result = poll(db, gr_settings, "shelf_variants_rebuilt.xml")

    assert result == PollResult("ok", 2, 7)
    assert count(db, "raw_archive") == 2
    assert checksum(snapshot(db)) == before


def test_changed_read_at_replaces_the_old_value_and_a_removed_book_stays(db, gr_settings):
    poll(db, gr_settings, "shelf_variants.xml")
    result = poll(db, gr_settings, "shelf_variants_edited.xml")
    assert result == PollResult("ok", 2, 6)
    stored = rows(db)
    assert count(db, "books") == 7
    assert stored["90000001"] == ("2026-02-18T05:00:00Z", "2026-02-18T03:52:40Z", 0)
    assert stored["90000003"] == ("2025-03-01T05:00:00Z", "2022-08-08T12:34:44Z", 0)
    assert stored["90000005"] == EXPECTED_ROWS["90000005"]


def test_empty_shelf_is_a_valid_feed_that_stores_nothing(db, gr_settings):
    assert poll(db, gr_settings, "shelf_empty.xml") == PollResult("ok", 1, 0)
    assert count(db, "books") == 0
    assert db.execute("SELECT parsed_ok FROM raw_archive").fetchone()[0] == 1


def test_malformed_feed_is_archived_and_flagged(db, gr_settings):
    result = poll(db, gr_settings, "malformed.xml")
    assert result == PollResult("malformed", 1)
    row = db.execute("SELECT parsed_ok, error, path FROM raw_archive").fetchone()
    assert row["parsed_ok"] == 0
    assert "malformed goodreads feed" in row["error"]
    assert (gr_settings.storage.raw_dir / row["path"]).read_bytes() == feed("malformed.xml")
    assert count(db, "books") == 0
    assert poll(db, gr_settings, "shelf_variants.xml") == PollResult("ok", 2, 7)


def test_not_an_rss_document_is_malformed():
    with pytest.raises(ValueError, match="not <rss><channel>"):
        parse_feed(b"<html><body>Sign in to Goodreads</body></html>")


@pytest.mark.parametrize("status", [301, 302, 307, 308])
def test_redirect_to_another_host_is_refused_and_not_followed(db, gr_settings, status):
    shelf = Shelf(b"", status, {"location": "https://cdn.example.net/shelf.xml"})
    with shelf.client() as client, pytest.raises(FeedError, match="refusing redirect"):
        poll_goodreads(db, gr_settings, client)
    assert [r.url.host for r in shelf.requests] == ["www.goodreads.com"]
    assert count(db, "raw_archive") == 0


def test_redirect_even_within_goodreads_is_refused(db, gr_settings):
    shelf = Shelf(b"", 302, {"location": "https://goodreads.com/review/list_rss/1"})
    with shelf.client() as client, pytest.raises(FeedError, match="refusing redirect"):
        poll_goodreads(db, gr_settings, client)
    assert len(shelf.requests) == 1


def test_a_client_set_to_follow_redirects_still_does_not(db, gr_settings):
    shelf = Shelf(b"", 302, {"location": "https://cdn.example.net/shelf.xml"})
    client = httpx.Client(transport=httpx.MockTransport(shelf.handle), follow_redirects=True)
    with client, pytest.raises(FeedError, match="refusing redirect"):
        fetch_feed(FEED_URL, client)
    assert len(shelf.requests) == 1


@pytest.mark.parametrize(
    "url",
    [
        "https://www.goodreads.com.example.net/review/list_rss/1",
        "https://cdn.example.net/shelf.xml",
        "http://www.goodreads.com/review/list_rss/1",
        "https://user@evil.example.net/x",
        "not a url at all",
    ],
)
def test_only_a_goodreads_https_url_is_ever_requested(db, gr_settings, url):
    shelf = Shelf(feed("shelf_empty.xml"))
    wrong = replace(gr_settings, goodreads_rss_url=url)
    with shelf.client() as client, pytest.raises(FeedError):
        poll_goodreads(db, wrong, client)
    assert shelf.requests == []
    assert count(db, "raw_archive") == 0


def test_http_error_archives_nothing(db, gr_settings):
    shelf = Shelf(b"Service Unavailable", 503)
    with shelf.client() as client, pytest.raises(httpx.HTTPStatusError):
        poll_goodreads(db, gr_settings, client)
    assert count(db, "raw_archive") == 0


def test_oversized_body_is_refused_and_not_archived(db, gr_settings, monkeypatch):
    monkeypatch.setattr(goodreads, "MAX_BYTES", 1000)
    shelf = Shelf(feed("shelf_variants.xml"))
    with shelf.client() as client, pytest.raises(FeedError, match="cap"):
        poll_goodreads(db, gr_settings, client)
    assert count(db, "raw_archive") == 0


def test_unconfigured_url_polls_nothing(db, settings):
    assert poll_goodreads(db, settings) == PollResult("unconfigured")
    assert count(db, "raw_archive") == 0


def test_replay_of_the_archive_reproduces_live(db, gr_settings, tmp_path, capsys):
    poll(db, gr_settings, "shelf_variants.xml")
    poll(db, gr_settings, "malformed.xml")
    poll(db, gr_settings, "shelf_variants_edited.xml")
    live = snapshot(db)
    assert len(live["books"]) == 7

    scratch = replay.replay(
        gr_settings.storage.raw_dir, tmp_path / "scratch.db", gr_settings, recorded=db
    )
    try:
        assert checksum(snapshot(scratch)) == checksum(live)
    finally:
        scratch.close()

    code = replay.main(["--verify", "--scratch", str(tmp_path / "verify.db")])
    out = capsys.readouterr().out
    assert code == 0, out
    assert "0 difference(s)" in out
    assert "unparsed: 1 recorded payload" in out and "goodreads/" in out


def test_replay_order_matters_and_is_kept(db, gr_settings, tmp_path):
    poll(db, gr_settings, "shelf_variants_edited.xml")
    poll(db, gr_settings, "shelf_variants.xml")
    assert rows(db)["90000001"] == EXPECTED_ROWS["90000001"]
    scratch = replay.replay(
        gr_settings.storage.raw_dir, tmp_path / "scratch.db", gr_settings, recorded=db
    )
    try:
        assert rows(scratch) == rows(db)
    finally:
        scratch.close()


def fixed_fetch(name: str, monkeypatch: pytest.MonkeyPatch, calls: list[str]) -> None:
    def fake(url: str, client: httpx.Client | None = None) -> bytes:
        calls.append(url)
        return feed(name)

    monkeypatch.setattr(goodreads, "fetch_feed", fake)


def test_job_is_registered_only_with_a_url(jobs_settings):
    without = jobs.build_scheduler(jobs_settings, lambda: open_db(jobs_settings.storage.db_path))
    assert {job.id for job in without.get_jobs()} == jobs.CORE_JOBS

    with_url = replace(jobs_settings, goodreads_rss_url=FEED_URL)
    built = jobs.build_scheduler(with_url, lambda: open_db(with_url.storage.db_path))
    by_id = {job.id: job for job in built.get_jobs()}
    assert set(by_id) == jobs.CORE_JOBS | {jobs.GOODREADS_JOB}
    job = by_id[jobs.GOODREADS_JOB]
    built.start(paused=True)
    try:
        job = built.get_job(jobs.GOODREADS_JOB)
        assert job.max_instances == 1 and job.coalesce
    finally:
        built.shutdown(wait=False)
    assert job.misfire_grace_time == jobs.GOODREADS_MISFIRE_GRACE_S
    assert jobs.GOODREADS_JOB in built.job_stats


def test_job_fires_at_the_configured_new_york_time(jobs_settings):
    with_url = replace(jobs_settings, goodreads_rss_url=FEED_URL)
    assert with_url.pull.goodreads == "06:30"
    built = jobs.build_scheduler(with_url, lambda: open_db(with_url.storage.db_path))
    cron = built.get_job(jobs.GOODREADS_JOB).trigger
    assert str(cron.timezone) == "America/New_York"
    after = cron.get_next_fire_time(None, datetime(2026, 10, 2, 12, 0, tzinfo=UTC))
    assert after == datetime(2026, 10, 3, 6, 30, tzinfo=NY)
    before = cron.get_next_fire_time(None, datetime(2026, 10, 2, 9, 0, tzinfo=UTC))
    assert before == datetime(2026, 10, 2, 6, 30, tzinfo=NY)
    winter = cron.get_next_fire_time(None, datetime(2026, 12, 2, 12, 0, tzinfo=UTC))
    assert winter.astimezone(UTC) == datetime(2026, 12, 3, 11, 30, tzinfo=UTC)


def test_a_bad_goodreads_time_skips_only_that_job(jobs_settings, caplog):
    typo = replace(
        jobs_settings,
        goodreads_rss_url=FEED_URL,
        pull=replace(jobs_settings.pull, goodreads="6h30"),
    )
    built = jobs.build_scheduler(typo, lambda: open_db(typo.storage.db_path))
    assert {job.id for job in built.get_jobs()} == jobs.CORE_JOBS
    assert "goodreads_poll not registered" in caplog.text


def test_overdue_poll_is_caught_up_soon_after_start(db, jobs_settings, monkeypatch):
    with_url = replace(jobs_settings, goodreads_rss_url=FEED_URL)
    open_conn = lambda: open_db(with_url.storage.db_path)  # noqa: E731
    now = datetime(2026, 10, 2, 16, 0, tzinfo=UTC)
    assert jobs.goodreads_overdue(open_conn, now)
    fresh = jobs.build_scheduler(with_url, open_conn, now)
    fresh.start(paused=True)
    assert fresh.get_job(jobs.GOODREADS_JOB).next_run_time == now + jobs.GOODREADS_CATCHUP_DELAY
    fresh.shutdown(wait=False)

    monkeypatch.setattr(health, "now_utc", lambda: now - timedelta(hours=9))
    poll(db, with_url, "shelf_variants.xml")
    assert not jobs.goodreads_overdue(open_conn, now)
    assert jobs.goodreads_overdue(open_conn, now + timedelta(days=2))
    settled = jobs.build_scheduler(with_url, open_conn, now)
    settled.start(paused=True)
    next_run = settled.get_job(jobs.GOODREADS_JOB).next_run_time
    local_next = next_run.astimezone(NY)
    assert (local_next.hour, local_next.minute) == (6, 30)
    assert timedelta(0) < next_run - datetime.now(UTC) <= timedelta(days=1)
    settled.shutdown(wait=False)

    monkeypatch.setattr(health, "now_utc", lambda: now - timedelta(hours=1))
    assert poll(db, with_url, "malformed.xml").status == "malformed"
    assert jobs.last_goodreads_poll(open_conn()) == now - timedelta(hours=9)


def test_job_runs_the_poll_on_a_fresh_connection_and_survives_a_failure(
    db, jobs_settings, monkeypatch
):
    with_url = replace(jobs_settings, goodreads_rss_url=FEED_URL)
    opened: list[sqlite3.Connection] = []

    def open_conn() -> sqlite3.Connection:
        conn = open_db(with_url.storage.db_path)
        opened.append(conn)
        return conn

    calls: list[str] = []
    fixed_fetch("shelf_variants.xml", monkeypatch, calls)
    built = jobs.build_scheduler(with_url, open_conn)
    run = built.get_job(jobs.GOODREADS_JOB).func

    run()
    assert calls == [FEED_URL]
    assert rows(db) == EXPECTED_ROWS
    assert built.job_stats[jobs.GOODREADS_JOB] == jobs.JobStats(runs=1)

    def broken(url: str, client: httpx.Client | None = None) -> bytes:
        raise FeedError("refusing redirect 302 toward 'cdn.example.net'")

    monkeypatch.setattr(goodreads, "fetch_feed", broken)
    run()
    stats = built.job_stats[jobs.GOODREADS_JOB]
    assert (stats.runs, stats.failures) == (2, 1)
    assert stats.last_error == "FeedError: refusing redirect 302 toward 'cdn.example.net'"
    assert rows(db) == EXPECTED_ROWS
    assert len(opened) >= 2 and opened[-1] is not opened[-2]
    for conn in opened:
        with pytest.raises(sqlite3.ProgrammingError):
            conn.execute("SELECT 1")


def test_sync_cli_says_clearly_when_unconfigured(settings, capsys):
    assert sync.main(["--source", "goodreads"]) == 2
    err = capsys.readouterr().err
    assert "GOODREADS_RSS_URL is not set" in err


def test_sync_cli_polls_with_a_url(db, settings, monkeypatch, capsys):
    monkeypatch.setenv("GOODREADS_RSS_URL", FEED_URL)
    calls: list[str] = []
    fixed_fetch("shelf_variants.xml", monkeypatch, calls)
    assert sync.main(["--source", "goodreads"]) == 0
    assert capsys.readouterr().out.strip() == "goodreads ok raw_archive_id=1 books=7"
    assert rows(db) == EXPECTED_ROWS
    assert sync.main(["--source", "goodreads"]) == 0
    assert capsys.readouterr().out.strip() == "goodreads duplicate raw_archive_id=1"

    fixed_fetch("malformed.xml", monkeypatch, calls)
    assert sync.main(["--source", "goodreads"]) == 1
    assert capsys.readouterr().out.strip() == "goodreads malformed raw_archive_id=2"

    def down(url: str, client: httpx.Client | None = None) -> bytes:
        raise httpx.ConnectError("no route to host")

    monkeypatch.setattr(goodreads, "fetch_feed", down)
    assert sync.main(["--source", "goodreads"]) == 1
    assert "goodreads fetch failed: ConnectError" in capsys.readouterr().err


def test_sync_cli_still_reads_claude_usage(db, settings, capsys, tmp_path, monkeypatch):
    usage = ClaudeUsageConfig(path=tmp_path / "absent.json", stale_hours=24)
    monkeypatch.setattr(sync, "load_settings", lambda: replace(settings, claude_usage=usage))
    assert sync.main(["--source", "claude_usage"]) == 0
    assert capsys.readouterr().out.strip() == "claude_usage missing"


def test_raw_archive_file_is_xml_next_to_the_json_sources(db, gr_settings):
    poll(db, gr_settings, "shelf_variants.xml")
    [archived] = list((gr_settings.storage.raw_dir / "goodreads").iterdir())
    assert archived.suffix == ".xml"
    assert Path(archived).read_bytes()[:5] == b"<?xml"
    plan = replay.replay_plan(gr_settings.storage.raw_dir, db)
    assert [source for source, _, _ in plan.entries] == ["goodreads"]
    assert plan.unrecorded == []
