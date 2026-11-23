"""Goodreads `read` shelf poller: the public shelf RSS is a source like any other.

The feed is fetched from the one configured URL (GOODREADS_RSS_URL in .env; the host must
be goodreads.com or www.goodreads.com, redirects are refused), archived verbatim as
`data/raw/goodreads/<utc stamp>_<sha8>.xml`, then parsed with the stdlib XML parser and
upserted into `books`. Identical bytes are a no-op: the feed's `lastBuildDate` is the time
of the last shelf change, not of the fetch, so an unchanged shelf fetches the same bytes
(checked against the live feed 2026-10-02: two fetches two seconds apart, one sha256).

Dates. `user_read_at` is the calendar date Max set on Goodreads, emitted as
`Tue, 17 Feb 2026 00:00:00 +0000`; its year-month-day is taken as written and stored as that
day's midnight in the home timezone, so `local_day()` of the stored instant is the day Max
chose. `user_date_added` is a real instant with an offset and is converted as such. Accepted
formats for both: RFC 822 as Goodreads writes it (weekday optional, day padded or not,
seconds optional, numeric offset or GMT/UT/UTC or a US zone name) and ISO 8601 (date, or
date and time with or without an offset); an offset-less instant is read as UTC. Anything
else is stored as null and logged, never guessed. The fallback rule
(`books.fallback_to_date_added`) applies when `user_read_at` is empty or unreadable:
`read_at` takes `date_added` and the row is marked `date_inferred = 1`; with the rule off,
`read_at` stays null.

Books YTD and the "finished a book" win are Phase 2 metrics; this module only stores.

The shelf widget (2026-11-15). Goodreads retired its shelf RSS feeds around 2026-10-28:
every `/review/list_rss/` URL answers 404 and the shelf page redirects to sign-in. The
public shelf widget, `/review/custom_widget/<user id>?shelf=read`, still answers without a
login, so the poll now fetches that (`shelf_url` derives it from the configured feed URL and
sends no key). It is a script that carries the shelf as an escaped HTML string: up to 100
books, most recently read first, each with a review id, a title and an author, and NO
dates. So for a widget payload:
- a book already stored (matched by title and author, a "(Series, #1)" suffix and case
  ignored, by title alone when the stored row has no author, or by its review id) is left
  exactly as it is: its dates came from the feed;
- a book not yet stored was added to the shelf since the last look, so its read day is
  the day its payload was received (`raw_archive.received_at_utc`, home timezone), stored
  like a feed read date and marked `date_inferred = 1`. That is the goal model's own rule
  ("the `read` shelf gained an entry") and it replays to the same day;
- on an empty `books` table nothing is known about when anything was read, so every book
  is stored undated and counts toward no day.
Archived widget payloads keep the source's `.xml` suffix; `apply_feed` tells the two forms
apart by their first byte.
"""

from __future__ import annotations

import html
import logging
import re
import sqlite3
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from app.config import Settings
from app.ingest.health import archive_raw, run_ingest
from app.timeutil import from_utc_iso, to_utc_iso

SOURCE = "goodreads"
ALLOWED_HOSTS = frozenset({"www.goodreads.com", "goodreads.com"})
TIMEOUT_S = 30.0
MAX_BYTES = 32 * 1024 * 1024
log = logging.getLogger(__name__)


class FeedError(RuntimeError):
    """The feed could not be fetched from the one allowed place."""


@dataclass(frozen=True)
class Book:
    id: str
    title: str
    author: str | None
    read_at_utc: str | None
    date_added_utc: str | None


WIDGET_PATH = "/review/custom_widget/{user}"
WIDGET_QUERY = (
    "num_books=100&order=d&shelf={shelf}&show_author=1&show_cover=0&show_rating=0"
    "&show_review=0&show_tags=0&show_title=1&sort=date_read"
)
_FEED_PATH = re.compile(r"^/review/list_rss/(\d+)")
_SHELF = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
WIDGET_MARK = "gr_custom_container_"
_WIDGET_CODE = re.compile(r"var\s+widget_code\s*=\s*'((?:[^'\\\n]|\\.)*)'", re.S)
_JS_ESCAPE = re.compile(r"\\(.)", re.S)
_JS_UNESCAPED = {"n": "\n", "t": "\t", "r": "\r"}
_WIDGET_BOOK = re.compile(
    r'gr_custom_title_">\s*<a[^>]*?/review/show/(\d+)[^>]*>(.*?)</a>'
    r'(?:(?!gr_custom_title_).)*?gr_custom_author_">\s*(?:by\s*)?<a[^>]*>(.*?)</a>',
    re.S,
)
_WIDGET_TITLE = re.compile(r'gr_custom_title_">')
_SERIES = re.compile(r"\s*\([^()]*#\s*\d+[^()]*\)\s*$")
_SPACE = re.compile(r"\s+")
REVIEW_ID_PREFIX = "review:"


@dataclass(frozen=True)
class ShelfBook:
    """One book as the shelf widget shows it: no dates, and a review id, not a book id."""

    review_id: str
    title: str
    author: str | None


def shelf_url(configured: str) -> str:
    """The URL the poll fetches. A retired shelf-feed URL (`/review/list_rss/<user id>`)
    becomes that user's shelf widget, same host, same shelf, WITHOUT the feed's key; any
    other URL is fetched as written."""
    try:
        target = httpx.URL(configured)
    except httpx.InvalidURL:
        return configured
    found = _FEED_PATH.match(target.path)
    if found is None:
        return configured
    shelf = target.params.get("shelf", "read")
    if not _SHELF.match(shelf):
        shelf = "read"
    path = WIDGET_PATH.format(user=found.group(1))
    return f"{target.scheme}://{target.host}{path}?{WIDGET_QUERY.format(shelf=shelf)}"


def is_widget(body: bytes) -> bool:
    """A widget payload is a script; a feed is XML and starts with '<'."""
    return not body.lstrip().startswith(b"<")


def parse_widget(body: bytes) -> list[ShelfBook]:
    """Every book of a shelf-widget script, in its order. ValueError when the bytes are not
    one, or when a title in it cannot be read whole (a changed layout must fail loudly, not
    drop books). An empty shelf is a valid widget with no books."""
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"malformed goodreads widget: {exc}") from exc
    code = _WIDGET_CODE.search(text)
    if code is None or WIDGET_MARK not in code.group(1):
        raise ValueError("malformed goodreads widget: no widget_code string with a shelf in it")
    markup = _JS_ESCAPE.sub(lambda m: _JS_UNESCAPED.get(m.group(1), m.group(1)), code.group(1))
    books = []
    for review_id, title, author in _WIDGET_BOOK.findall(markup):
        name = _SPACE.sub(" ", html.unescape(re.sub(r"<[^>]+>", "", title))).strip()
        by = _SPACE.sub(" ", html.unescape(re.sub(r"<[^>]+>", "", author))).strip()
        if name:
            books.append(ShelfBook(review_id, name, by or None))
    titles = len(_WIDGET_TITLE.findall(markup))
    if len(books) != titles:
        raise ValueError(
            f"malformed goodreads widget: {titles} titles but {len(books)} readable books"
        )
    return books


def match_key(title: str, author: str | None) -> tuple[str, str]:
    """What makes two listings the same book across the feed and the widget: the title
    without a trailing "(Series, #1)" and the author, spaces collapsed, case ignored."""
    bare = _SERIES.sub("", title)
    return (
        _SPACE.sub(" ", bare).strip().casefold(),
        _SPACE.sub(" ", author or "").strip().casefold(),
    )


def first_seen_dates(received_at_utc: str, home_tz: str) -> tuple[str, str]:
    """(read_at, date_added) for a book first seen in a payload received then: the home-
    timezone day's midnight, as a feed read date is stored, and the instant itself."""
    received = from_utc_iso(received_at_utc)
    local = received.astimezone(ZoneInfo(home_tz))
    midnight = datetime(local.year, local.month, local.day, tzinfo=ZoneInfo(home_tz))
    return to_utc_iso(midnight), to_utc_iso(received)


def store_shelf(
    conn: sqlite3.Connection, books: list[ShelfBook], received_at_utc: str, home_tz: str
) -> int:
    """Add the widget's books that are not stored yet; touch nothing that is. A new book is
    dated the day it was first seen and marked inferred, unless the table was empty, when
    nothing is known and it is stored undated. Returns the number of rows added."""
    rows = conn.execute("SELECT id, title, author FROM books").fetchall()
    known_ids = {str(row["id"]) for row in rows}
    known = {match_key(str(row["title"]), row["author"]) for row in rows}
    authorless = {title for title, author in known if not author}
    read_at, date_added, inferred = (None, None, 0)
    if rows:
        read_at, date_added = first_seen_dates(received_at_utc, home_tz)
        inferred = 1
    added = 0
    for book in books:
        book_id = REVIEW_ID_PREFIX + book.review_id
        key = match_key(book.title, book.author)
        if book_id in known_ids or key in known or key[0] in authorless:
            continue
        conn.execute(
            "INSERT INTO books (id, title, author, read_at, date_added, date_inferred) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (book_id, book.title, book.author, read_at, date_added, inferred),
        )
        known_ids.add(book_id)
        known.add(key)
        added += 1
    return added


@dataclass(frozen=True)
class PollResult:
    status: str
    raw_archive_id: int | None = None
    books: int = 0


def fetch_feed(url: str, client: httpx.Client | None = None) -> bytes:
    """GET the shelf feed. The only outbound call this module makes.

    Refuses a URL whose host is not Goodreads over https, refuses any redirect (whatever its
    target), and refuses a body over MAX_BYTES. Raises FeedError for those and httpx.HTTPError
    for transport and status failures; the caller decides what to log.
    """
    try:
        target = httpx.URL(url)
    except httpx.InvalidURL as exc:
        raise FeedError(f"GOODREADS_RSS_URL is not a URL: {exc}") from exc
    if target.scheme != "https" or target.host not in ALLOWED_HOSTS:
        raise FeedError(f"refusing to fetch from {target.host!r}: not a Goodreads https URL")
    own = client is None
    session = client or httpx.Client(timeout=TIMEOUT_S, trust_env=False)
    try:
        request = session.build_request("GET", target)
        response = session.send(request, follow_redirects=False)
        try:
            if response.is_redirect or response.next_request is not None:
                where = httpx.URL(response.headers.get("location", "")).host or "?"
                raise FeedError(
                    f"refusing redirect {response.status_code} toward {where!r}; "
                    "the feed must be served from the configured URL"
                )
            if response.url.host not in ALLOWED_HOSTS:
                raise FeedError(f"response came from {response.url.host!r}, not Goodreads")
            response.raise_for_status()
            if len(response.content) > MAX_BYTES:
                raise FeedError(f"feed is {len(response.content)} bytes, over the {MAX_BYTES} cap")
            return response.content
        finally:
            response.close()
    finally:
        if own:
            session.close()


def parse_feed_date(text: str | None) -> datetime | None:
    """A feed date as an aware datetime, or None when empty or in no accepted format."""
    value = (text or "").strip()
    if not value:
        return None
    parsed: datetime | None = None
    try:
        parsed = parsedate_to_datetime(value)
    except (ValueError, TypeError, IndexError):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def read_date_utc(text: str | None, home_tz: str) -> str | None:
    """`user_read_at` as the chosen calendar day's local midnight, in UTC ISO."""
    parsed = parse_feed_date(text)
    if parsed is None:
        return None
    local_midnight = datetime(parsed.year, parsed.month, parsed.day, tzinfo=ZoneInfo(home_tz))
    return to_utc_iso(local_midnight)


def instant_utc(text: str | None) -> str | None:
    parsed = parse_feed_date(text)
    return None if parsed is None else to_utc_iso(parsed)


def _text(item: ET.Element, tag: str) -> str | None:
    value = item.findtext(tag)
    return value.strip() if value and value.strip() else None


def _book_id(item: ET.Element) -> str | None:
    listed = _text(item, "book_id")
    if listed:
        return listed
    nested = item.find("book")
    inner = (nested.get("id") or "").strip() if nested is not None else ""
    return inner or None


def parse_feed(body: bytes, home_tz: str = "America/New_York") -> list[Book]:
    """Every item of the shelf as a Book. ValueError when the bytes are not a Goodreads feed.

    An item with no usable id (no `book_id`, no `<book id>`) or no title is skipped and
    logged; it cannot be keyed or shown. An empty shelf is a valid feed with no books.
    """
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        raise ValueError(f"malformed goodreads feed: {exc}") from exc
    channel = root.find("channel") if root.tag == "rss" else None
    if channel is None:
        raise ValueError(f"malformed goodreads feed: root is <{root.tag}>, not <rss><channel>")
    books: list[Book] = []
    for item in channel.findall("item"):
        book_id = _book_id(item)
        title = _text(item, "title")
        if book_id is None or title is None:
            log.warning("skipped a feed item with no %s", "book id" if book_id is None else "title")
            continue
        read_at = _text(item, "user_read_at")
        read_at_utc = read_date_utc(read_at, home_tz)
        if read_at and read_at_utc is None:
            log.warning("book %s: user_read_at %r is in no accepted format", book_id, read_at)
        added = _text(item, "user_date_added")
        date_added_utc = instant_utc(added)
        if added and date_added_utc is None:
            log.warning("book %s: user_date_added %r is in no accepted format", book_id, added)
        books.append(Book(book_id, title, _text(item, "author_name"), read_at_utc, date_added_utc))
    return books


def stored_dates(book: Book, fallback_to_date_added: bool) -> tuple[str | None, int]:
    """(read_at, date_inferred) as the books row carries them."""
    if book.read_at_utc is not None:
        return book.read_at_utc, 0
    if fallback_to_date_added and book.date_added_utc is not None:
        return book.date_added_utc, 1
    return None, 0


def store_books(
    conn: sqlite3.Connection, books: list[Book], fallback_to_date_added: bool = True
) -> int:
    """Upsert every book. The feed is authoritative for the fields it carries, so a read-at
    date changed on Goodreads replaces the stored one. A book that left the shelf is NOT
    deleted: the feed shows the shelf as it is, and a row that stops appearing keeps what
    was last seen. Returns the number of rows written."""
    for book in books:
        read_at, inferred = stored_dates(book, fallback_to_date_added)
        conn.execute(
            "INSERT INTO books (id, title, author, read_at, date_added, date_inferred) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (id) DO UPDATE SET title = excluded.title, author = excluded.author, "
            "read_at = excluded.read_at, date_added = excluded.date_added, "
            "date_inferred = excluded.date_inferred",
            (book.id, book.title, book.author, read_at, book.date_added_utc, inferred),
        )
    return len(books)


def apply_feed(
    conn: sqlite3.Connection, body: bytes, raw_archive_id: int, settings: Settings, first: bool
) -> int:
    """Parse and store one payload, a feed or a shelf widget, inside the caller's
    transaction."""
    if is_widget(body):
        row = conn.execute(
            "SELECT received_at_utc FROM raw_archive WHERE id = ?", (raw_archive_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"no raw_archive row {raw_archive_id} to date a shelf widget by")
        shelf = parse_widget(body)
        return store_shelf(conn, shelf, str(row["received_at_utc"]), settings.home_tz)
    books = parse_feed(body, settings.home_tz)
    return store_books(conn, books, settings.books.fallback_to_date_added)


def ingest_archived(
    conn: sqlite3.Connection,
    body: bytes,
    raw_archive_id: int,
    settings: Settings,
    raw_dir: Path | None = None,
) -> int:
    """Parse and store an already-archived feed, keeping raw_archive.id order."""
    stored, _ = run_ingest(conn, body, raw_archive_id, settings, apply_feed, raw_dir)
    assert isinstance(stored, int)
    return stored


def poll_goodreads(
    conn: sqlite3.Connection, settings: Settings, client: httpx.Client | None = None
) -> PollResult:
    """Fetch the shelf, archive it, store its books. Unchanged bytes are a no-op.

    Fetch failures (FeedError, httpx.HTTPError) propagate: nothing was archived, there is
    nothing to mark. A feed that archived but will not parse is marked in raw_archive.
    """
    url = settings.goodreads_rss_url
    if not url:
        return PollResult("unconfigured")
    body = fetch_feed(shelf_url(url), client)
    archived = archive_raw(conn, settings.storage.raw_dir, body, source=SOURCE)
    if archived.duplicate:
        return PollResult("duplicate", archived.raw_archive_id)
    try:
        stored = ingest_archived(conn, body, archived.raw_archive_id, settings)
    except ValueError:
        return PollResult("malformed", archived.raw_archive_id)
    return PollResult("ok", archived.raw_archive_id, stored)
