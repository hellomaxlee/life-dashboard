# Goodreads `read` shelf fixtures (SYNTHETIC)

Shaped after one fetch of the real shelf RSS on 2026-10-02 (the URL is the secret
`GOODREADS_RSS_URL`; the real feed is not committed). Titles, authors, ids and dates here are
invented; the element layout, CDATA use, entity escaping, blank fields and date formats are
copied from what Goodreads emitted.

## The real feed, 2026-10-02

| Fact | Value |
|---|---|
| Root | `<rss version="2.0">` → `<channel>` → `<item>`s; `lastBuildDate` is the last shelf change (Feb 2026), `ttl` 60 |
| Items | 75, all with a non-empty `book_id` (unique) and `title` |
| `user_read_at` non-empty | 19 (years: 2023 ×2, 2024 ×11, 2025 ×5, 2026 ×1); always `00:00:00 +0000`; the day is unpadded for 1–9 (`Fri, 3 Nov 2023 …`), padded otherwise |
| `user_date_added` non-empty | 75; always `Www, DD Mon YYYY HH:MM:SS -0700/-0800` (Goodreads' Pacific clock); 1 in 2026 |
| `user_date_created`, `pubDate` | 75, same format as `date_added`; `pubDate` equals `user_date_added` |
| `isbn` | blank for 14 |
| `book_published` | blank for 4 |
| `user_shelves`, `user_review` | mostly blank (5 and 7 filled) |
| `<book id="…"><num_pages>` | nested element carrying the same id |
| Titles | mostly CDATA, some plain with `&apos;`; one with a literal `&` inside CDATA, one with a curly apostrophe |
| Size | 298,125 bytes, `application/xml; charset=utf-8`, HTTP 200, no redirect |
| Two fetches 2 s apart | byte-identical (one sha256): an unchanged shelf is a duplicate archive, no canonicalisation needed |

## Files

| File | What it exercises |
|---|---|
| `shelf_variants.xml` | 9 items → 7 books. `90000001` read-at padded day, Pacific date-added; `90000002` read-at **unpadded** day, title and author with `&apos;`; `90000003` **no read-at** (empty CDATA), title with `&` inside CDATA, non-ASCII author, blank isbn and `book_published` → date inferred from date-added; `90000004` no read-at (empty element), numeric `&#8217;`, `&quot;` and `&amp;` entities → inferred; `90000005` read-at 31 Dec 2025 but date-added 21:30 Pacific = 1 Jan 2026 New York (the year-boundary case for Phase 2: it counts in 2025); `90000006` **unreadable** read-at (`30 Feb`) and date-added (`sometime in June`) → both null, nothing inferred; `90000007` empty `book_id` but `<book id>` set, ISO 8601 dates, empty author; `90000008` no id anywhere → skipped; `90000009` empty title → skipped |
| `shelf_variants_rebuilt.xml` | The same shelf with a different `lastBuildDate`: a byte-different feed that must change no data table |
| `shelf_variants_edited.xml` | `90000001` read-at moved to 18 Feb; `90000003` gained a read-at (1 Mar 2025, so it is no longer inferred); `90000005` removed from the shelf (its row must stay) |
| `shelf_empty.xml` | A channel with no items: valid, stores nothing |
| `malformed.xml` | `shelf_variants.xml` cut mid-element: archived, `parsed_ok = 0`, error recorded |

Expected rows after `shelf_variants.xml` with `books.fallback_to_date_added = true`
(read-at is the chosen day's 00:00 America/New_York as UTC; date-added is the instant as UTC):

| id | read_at | date_added | date_inferred |
|---|---|---|---|
| 90000001 | 2026-02-17T05:00:00Z | 2026-02-18T03:52:40Z | 0 |
| 90000002 | 2023-11-03T04:00:00Z | 2023-11-04T05:17:37Z | 0 |
| 90000003 | 2022-08-08T12:34:44Z | 2022-08-08T12:34:44Z | 1 |
| 90000004 | 2024-01-17T17:15:37Z | 2024-01-17T17:15:37Z | 1 |
| 90000005 | 2025-12-31T05:00:00Z | 2026-01-01T05:30:00Z | 0 |
| 90000006 | null | null | 0 |
| 90000007 | 2026-03-08T05:00:00Z | 2026-03-08T10:30:00Z | 0 |
