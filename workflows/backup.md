# backup — nightly copy of the db and the raw archive, and how to prove it restores

Owner: Callum Reid. QA: Bartek. Tool: `tools/backup.py`. Scheduled by the
service at `backup.time` (03:15 America/New_York); see `workflows/run-service.md`
sections 10 to 12. Backups are local paths only: health data stays home.

## What a backup is

A directory, `data/backups/life-<UTC stamp>/` (the stamp is UTC; 03:15 New York
is `T071500Z` in summer, `T081500Z` in winter):

| File | Contents |
|---|---|
| `life.db` | the whole SQLite db, taken with the sqlite3 backup API. Safe while the service writes; committed data only, WAL included |
| `raw/` | every file under `data/raw/`, same layout. A file already in the previous backup with the same size is hard-linked to it, not copied again |
| `manifest.json` | `created_at_utc`, `data_checksum` (sha256 over the data tables, the same checksum `tools.replay` prints), `raw_files`, `raw_bytes`, `raw_linked`, `db_bytes` |

Order: db first, archive second. So every `raw_archive` row in a backup has its
file; the archive may hold a file or two newer than the db copy. The directory
is written as `life-<stamp>.partial` and renamed when whole; a `.partial` is an
interrupted backup and the next nightly run deletes it.

Retention: the nightly run keeps the newest `backup.keep` (14) `life-*`
directories under `backup.dir` and deletes the rest. `--out PATH` backups are
never pruned. Because raw files are hard-linked across nights, 14 nights cost
one copy of the archive plus 14 copies of the db, not 14 archives. The first
backup in a directory is always a full, independent copy of the live archive.

## Commands

```sh
cd /Users/maxwelllee12/life-dashboard
uv run python -m tools.backup                         # nightly backup now + retention
uv run python -m tools.backup --out PATH              # one backup at exactly PATH
uv run python -m tools.backup --verify PATH           # exit 0 identical, 1 different, 2 not a backup
uv run python -m tools.backup --verify PATH --no-replay   # skip the raw replay (faster)
uv run python -m tools.backup --restore PATH --to DIR     # writes DIR/life.db and DIR/raw/
uv run python -m tools.backup --restore PATH --to DIR --overwrite        # DIR/life.db exists, not live
uv run python -m tools.backup --restore PATH --to data --overwrite-live  # replace the live db
```

## What `--verify` checks

It restores the backup into a temp directory (never the live one) and reports
every difference, one per line, then `N difference(s)`:

1. `manifest:` the restored db's data checksum is not the one recorded at backup time (the backup changed on disk).
2. `raw_archive N: file missing` / `sha256 mismatch`: a recorded payload is absent or altered in the backup's `raw/`.
3. `live vs backup: <table>: +/- <row>`: a data-table row differs from the live db. `+` is in live only, `-` is in the backup only. Snapshot and checksum come from `tools/replay.py`.
4. `raw replay vs backup:` re-ingesting the backup's recorded raw files from scratch does not rebuild the backup's data tables. This is the second route to the same truth: the db copy and the archive must agree.

A push between backup and verify makes check 3 report the new rows. That is the
gate working; back up again and verify the new one.

## Restore

`--restore PATH --to DIR` copies `life.db` and `raw/` into `DIR`, removes any
stale `life.db-wal` / `life.db-shm` there, and runs `integrity_check`. It
refuses an existing `DIR/life.db` unless `--overwrite`, and refuses the live db
unless `--overwrite-live`; stop the service first (run-service section 12).
`raw_archive.path` keeps the absolute paths recorded at ingest, so a restore to
a different directory is for inspection and diffing; restoring into `data/`
puts every file back where its row says it is.

## Moving to another machine

Copy one backup directory across, `--restore` it into the new checkout's
`data/`, start the service. The phone's next push refills anything newer.

## Proof

`uv run pytest tests/tools/test_backup.py -q`. The gate's mutants are in the
suite: one altered row in the backup db, a live db that moved on, an altered
and a missing raw file each turn `--verify` red.
