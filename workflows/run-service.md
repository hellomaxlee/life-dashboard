# run-service — keeping life-dashboard up on the Mac

Owner: Callum Reid. QA: Bartek. Every command below is meant to be pasted
verbatim at 07:00 on a Sunday. Repo root is `/Users/maxwelllee12/life-dashboard`;
the Mac's LAN IP is `192.168.1.171`; the service listens on `0.0.0.0:8080`.

The unit is a **LaunchAgent** (user domain `gui/$(id -u)`), not a LaunchDaemon:
it starts at login, runs as Max, and can read `.env` and `~/.local/bin/uv`.
Files: `additional/launchd/com.maxlee.life-dashboard.plist`, `install.sh`,
`uninstall.sh`.

## 1. Install (first time, and after any plist edit)

```sh
cd /Users/maxwelllee12/life-dashboard
uv sync                                  # venv must exist before launchd starts uv run
bash additional/launchd/install.sh       # mkdir data/logs, copy plist, bootout, bootstrap, kickstart -k
```

`install.sh` is idempotent. The last two blocks it prints are the `launchctl print`
header (look for `state = running` and a `pid =` line) and the `lsof` listener line.

## 2. Is it up?

```sh
launchctl print gui/$(id -u)/com.maxlee.life-dashboard | head -20
lsof -nP -iTCP:8080 -sTCP:LISTEN         # expect one python line, *:8080 (LISTEN)
curl -s http://127.0.0.1:8080/healthz    # local
curl -s http://192.168.1.171:8080/healthz  # the address the phone uses
```

Both curls must answer. If the first works and the second does not, it is the
firewall or the IP (sections 6 and 7), not the service.

## 3. Logs

```sh
tail -f data/logs/life-dashboard.out.log     # uvicorn access + app log
tail -f data/logs/life-dashboard.err.log     # tracebacks, uv resolution errors
tail -n 50 data/logs/*.err.log
```

launchd appends; nothing rotates these yet. Once a month:
`: > data/logs/life-dashboard.out.log` after a `launchctl kickstart -k`.

## 4. Restart

```sh
launchctl kickstart -k gui/$(id -u)/com.maxlee.life-dashboard   # kill + immediate relaunch
```

After `git pull` that touched dependencies: `uv sync` first, then kickstart.
After a `git pull` that brought a new file under `app/migrations/`, or changed
anything under `app/ingest/`, follow section 16 (Upgrading) instead of a bare
kickstart.
`KeepAlive` is true, so a crash relaunches within `ThrottleInterval` (10 s). A
crash loop shows as a climbing `runs =` and `last exit code` in `launchctl print`.

## 5. Uninstall

```sh
bash additional/launchd/uninstall.sh     # bootout + rm ~/Library/LaunchAgents/com.maxlee.life-dashboard.plist
```

Leaves `data/` (db, raw archive, logs) in place.

## 6. macOS firewall

System Settings > Network > Firewall. If it is on, open Options and either
allow incoming connections for **Python** (the interpreter under
`.venv/bin/python`, which is what uvicorn is) or turn **Block all incoming
connections** off. The first time the service binds, macOS may pop a
"Do you want the application python to accept incoming network connections?"
dialog; click Allow. The venv python path changes when the venv is rebuilt, so
re-check after `rm -rf .venv && uv sync`.

Test from another device on the same Wi-Fi:
`curl -s http://192.168.1.171:8080/healthz`.

## 7. Router: reserve 192.168.1.171

Health Auto Export posts to a fixed IP. In the router's DHCP settings, add a
reservation binding the Mac's Wi-Fi MAC address (`ifconfig en0 | grep ether`) to
`192.168.1.171`. Without it the IP can move after a lease expiry and every
push fails silently until the phone's automation is edited.

Check the current IP: `ipconfig getifaddr en0`.

## 8. Sleep: the Mac must be awake to receive a push

A sleeping Mac drops the POST. Pushes come every 6 hours, so two automations
lose at most one cycle each while asleep, and **each push carries the previous
7 days**, so any missed window is refilled by the next successful push and
ingest is idempotent, so the overlap costs nothing. Still, prefer awake:

- Recommended: System Settings > Battery (laptop) or Energy (desktop) >
  **Prevent automatic sleeping when the display is off** (laptop: under
  Options, applies on power adapter). Leave the display allowed to sleep.
- Alternative, plugged in only: `sudo pmset -c sleep 0` (charger profile) or
  `sudo pmset -a sleep 0` (all profiles). Check with `pmset -g`.
- Wake-for-network-access is not enough on its own; it wakes for Bonjour, not
  for an arbitrary TCP connect on :8080.

## 9. Phone reports a failed push

In order, stop at the first failure:

1. Same Wi-Fi? The phone must be on the LAN, not cellular or a guest SSID.
   Open `http://192.168.1.171:8080/healthz` in Safari on the phone.
2. IP still right? `ipconfig getifaddr en0` on the Mac should print
   `192.168.1.171` (section 7).
3. Service running? `launchctl print gui/$(id -u)/com.maxlee.life-dashboard | head -20`
   and `lsof -nP -iTCP:8080 -sTCP:LISTEN`.
4. Firewall (section 6). `curl` from a second device is the discriminating test.
5. Mac asleep at push time (section 8).
6. Service up but rejecting: `tail -n 100 data/logs/*.err.log` for a 4xx/5xx on
   `/ingest/health`; a raw copy of the payload is in `data/raw/health/` if the
   request reached the archive step. Request Timeout in the app is 60 s.

## 10. Scheduler (runs inside the service)

The service starts an APScheduler thread at boot and stops it at shutdown; there
is no separate process to supervise. Config: `[scheduler]` and `[backup]` in
`config.toml`. Health needs no job (the phone pushes).

| Job id | When | What |
|---|---|---|
| `claude_usage_watch` | every `scheduler.usage_poll_seconds` (30 s) | stats `data/claude_usage.json`; reads it only if mtime or size changed, and at most once per `scheduler.usage_min_read_seconds` (900 s) |
| `nightly_backup` | `backup.time` (03:15 America/New_York) | `tools.backup` nightly: `data/backups/life-<utc stamp>/`, keeps the newest `backup.keep` (14) |
| `backup_overdue_check` | two minutes after start, then every hour | takes a backup if none is newer than 26 hours; otherwise does nothing |
| `device_rotation` | after each screen's hold (fallback: every `device.screen_seconds`), **only while `device.pixoo_host` is set** | sends the next screen (Week, Today, a sparkle per earned small win, Books, wrapping) to the Pixoo once the last one's hold is up; section 15 |
| `metrics_recompute` | 60 s after start, then every `metrics.recompute_minutes` (15 min) | recomputes every `daily_metrics` / `weekly_metrics` row from the stored tables; picks up whatever the last push brought; section 17 |
| `metrics_rollover` | `metrics.rollover_time` (00:05 America/New_York) | the same recompute, so the new day's and (on Monday) the new week's rows exist when the renderer looks them up |
| `goodreads_poll` | `pull.goodreads` (06:30 America/New_York), **only while `GOODREADS_RSS_URL` is set in `.env`**; plus once, three minutes after start, when no parsed feed arrived in the last 24 h | fetches the `read` shelf RSS, archives it, upserts `books`; section 17 |
| `daily_summary` | `summary.time` (06:50 America/New_York), misfire grace 12 h | writes the line shown today, describing yesterday (the latest complete day), into today's `daily_metrics` row (model if a key is set and the cap allows, rule-based copy otherwise); a day that already has a line is not regenerated; section 18 |

A job never overlaps itself, opens its own db connection per run, and an
exception is logged (`job <id> failed` plus traceback in the err log), not fatal.

```sh
grep -n "job .* failed" data/logs/life-dashboard.err.log | tail     # any job failure
ls -lt data/backups | head                                          # did last night's backup land
ls data/raw/claude_usage | wc -l                                    # usage readings archived
LIFE_SCHEDULER_ENABLED=0 uv run uvicorn app.main:app --host 127.0.0.1 --port 8080   # run by hand with no jobs (stop the LaunchAgent first)
```

`LIFE_SCHEDULER_ENABLED` takes `1/true/yes` or `0/false/no`; anything else stops the
service at boot with a message naming the variable. So does a `config.toml` value
that cannot work (`usage_poll_seconds` below 1, `keep` below 1 or not whole,
`enabled` in quotes, an empty `backup.dir`, an unknown `home_tz`): the error names
the key. Under launchd that shows as a crash loop (section 4) with the message in
the err log. A backup that found raw files
missing from the live archive still lands, and logs
`backup life-... is missing N raw file(s): ...` to the err log.

Mac asleep at 03:15: the backup runs within 30 s of the next wake if that is
inside 18 hours. Past that, or after a failed run, the hourly
`backup_overdue_check` takes one as soon as the newest backup is older than
26 hours; on a first boot that is two minutes after start. At shutdown the
service waits up to 10 s for a running job, logs `a job is still running` and
goes on without it; a job stuck beyond that is ended by launchd's kill.
A usage file over 64 KB, or one that is not the hook's JSON, is logged and
skipped. Reading the usage file by hand still works:
`uv run python -m tools.sync --source claude_usage`.

## 11. Backup

Full detail: `workflows/backup.md`. Safe while the service is running.

```sh
cd /Users/maxwelllee12/life-dashboard
uv run python -m tools.backup                                  # what the 03:15 job does, now
uv run python -m tools.backup --verify latest                 # newest complete backup; exit 0 = identical
uv run python -m tools.backup --out /Volumes/SomeDrive/life-backup-2026-10-02     # extra copy, local path
```

`latest` is the newest complete backup under `backup.dir`; it never names a
`.partial`. `--verify` checks the backup's files against its manifest, restores
to a temp dir, opens the copy the way the service would, diffs every data table
and the `raw_archive` state against the live db, and replays the backup's raw
archive from scratch; `0 difference(s)` and exit 0 is the pass (`note:` lines
are information, not differences). A push that lands between the backup and
the verify shows up as `live vs backup: ... +` lines; take a new backup and
verify that one. Exit 2 means the backup could not be read at all.

Is what is stored what the archive says? (the idempotency proof, any time):

```sh
uv run python -m tools.replay --verify      # replays data/raw into data/replay/scratch.db, diffs against live
```

## 12. Restore

Look first, into a scratch directory (never touches the live db):

```sh
uv run python -m tools.backup --restore latest --to /tmp/life-restore
sqlite3 /tmp/life-restore/life.db "PRAGMA integrity_check; SELECT COUNT(*) FROM activities;"
```

Replace the live db (disaster only). Stop the service first, or launchd will
hold the old file open:

```sh
launchctl bootout gui/$(id -u)/com.maxlee.life-dashboard
uv run python -m tools.backup --restore latest --to data --overwrite-live
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.maxlee.life-dashboard.plist
curl -s http://127.0.0.1:8080/healthz
```

Without `--overwrite-live` the command refuses and exits 2, however the path to
`data` is spelled. `/tmp/life-restore` must not already hold a different copy:
an existing `life.db`, or a raw file whose content differs, is refused unless
`--overwrite` is given. Anything pushed after the backup is refilled by the
phone's next push (each carries 7 days). A restored copy is self-contained: the
service started on it reads and writes raw files under its own `raw/` only.

## 13. Kill-mid-sync drill (`kill -9`, automated)

Claim: a `kill -9` at any point of an ingest loses nothing. The drill runs in
its own temp directory; it never opens `data/`. It is a process kill, **not a
power cut**: the OS survives and flushes its cache. Power-cut durability rests
on SQLite WAL mode and the fsync of each raw file before its row is written;
no test here cuts power.

```sh
uv run python -m tools.drill                     # all four stages, exit 1 on any failure
uv run python -m tools.drill --stage mid_transaction --payload fixtures/health/batch_part1.json
uv run pytest tests/jobs -q                      # the same drill plus restart and scheduler tests
```

A child process posts a payload to the real `/ingest/health` route, stops at
the named stage, and is SIGKILLed there. Stages: `before_row` (raw file on
disk, no `raw_archive` row), `after_archive` (row with `parsed_ok = 0`),
`mid_transaction` (rows written, not committed), `after_commit` (committed,
WAL not checkpointed: a `kill -9` right after a push). Pass, per stage:
`integrity_check` is `ok`, the raw file is on disk byte-for-byte, no partial
rows survived, re-posting the same payload answers `ok` (`duplicate` for
`after_commit`), and the data-table checksum equals a clean run's.

What the states mean on the real box:

- A raw file with no `raw_archive` row ("unrecorded") is a push whose bytes
  reached the disk but whose row did not: the process died in between, or the
  db was locked past the 5 s busy timeout (the phone got a 503 `busy`). Its
  data is **not** in the db. It is never applied silently and never ignored
  silently: `tools.replay --verify` and `tools.backup --verify` print it as a
  `note: unrecorded: ...` line with the file name. When the same bytes arrive
  again the retry adopts that file and records it. If they never do, the
  phone's later pushes carry the same days (7-day window).
- A row with `parsed_ok = 0` is a push that died mid-parse or failed. Its data
  is not in the db. Re-posting the same bytes parses it, then re-applies every
  later parsed payload of the same source on top, in one transaction, so older
  data fills gaps and newer data still wins; the answer is `ok` with a
  `reapplied` count.
- A parsed `raw_archive` row whose file is missing, or no longer matches its
  recorded sha256, is the combination that is a bug: that data can no longer be
  recomputed from a trusted source. Both `--verify` commands report it
  (`missing raw file` / `altered raw file`) and exit 1, and a late recovery that
  would need such a file stops with a 500 and applies nothing.

After a real power cut or a hard reset (untested by the drill; these are the checks to run):

```sh
sqlite3 data/life.db "PRAGMA integrity_check;"            # must print ok
sqlite3 data/life.db "SELECT id, source, received_at_utc, error FROM raw_archive WHERE parsed_ok = 0;"
uv run python -m tools.replay --verify
uv run python -m tools.backup && uv run python -m tools.backup --verify latest
```

## 14. Egress

`uv run pytest tests/test_egress.py -q` proves, statically, that nothing under
`app/` or `tools/` names a host outside `{www.goodreads.com, goodreads.com,
api.anthropic.com}` plus loopback and `192.168.x.x`, that nothing imports
`requests`, `urllib.request`, `socket`, `http.client`, `ftplib`, `smtplib` or
`telnetlib`, and that nothing starts `curl`, `wget` or `nc`. The gate carries
its own mutants (`test_gate_catches_*`) so a scanner regression turns red, not
green. It is a static scan: a host assembled at runtime is outside what it can
see.

## 15. Display (Pixoo-64)

Connected 2026-10-04: `[device] pixoo_host = "192.168.1.185"` in `config.toml`, so the
`device_rotation` job is registered and the panel shows the live rotation. Empty the value
and restart to turn it off: no job, no adapter, nothing sent. `LIFE_PIXOO_HOST` overrides
the file (the test suite sets it empty so no test can reach the panel).

This panel does not speak the community-documented Pixoo-64 API (port 80, `/post`): it
answers JSON POSTs on **port 9000 at `/divoom_api`** with `ReturnCode` 0 on success. Find
its IP in the Divoom app under the device's settings and reserve it in the router (as in
section 7); if the IP changes, the display freezes on its last screen and the err log says
`job device_rotation failed`.

Hand check, with the service untouched (fixture data unless `--date`):

```sh
uv run python -m tools.pixoo_check --host 192.168.1.185
uv run python -m tools.pixoo_check --host 192.168.1.185 --screen books --date 2026-10-04
grep -n "device_rotation" data/logs/life-dashboard.err.log | tail   # failures and refusals
```

A plain shell on this Mac cannot reach the panel ("No route to host", macOS local-network
privacy); a launchd job started through the same `uv` the service uses can. To run a probe
script by hand, and to remove the job afterwards (launchd restarts it otherwise):

```sh
launchctl submit -l com.maxlee.probe -o /tmp/probe.log -e /tmp/probe.log -- \
  /Users/maxwelllee12/.local/bin/uv run --directory /Users/maxwelllee12/life-dashboard python /path/to/probe.py
launchctl remove com.maxlee.probe
```

What the job does: Today, City, Week, Month, Books, one sparkle for each small win the shown
day earned, the party on a completed week, wrapping, each rendered for today's
America/New_York day from a fresh db connection. A still stays `screen_seconds`; Books
(pages are 2 s each) stays until it has played through; a sparkle is three stills held 0.3 s
each and the party six, sent one at a time like Books pages, so neither ever shows the
panel's loading cycle (which runs for as long as a multi-frame animation takes to upload:
30 s for the 20-frame sparkle they replaced, 2026-10-07). A celebration step is on screen
for its hold plus the 1.45 s the next still takes to upload, so a sparkle runs about 5 s and
the party about 10; four steps held a second each ran 10 s and read as lag (Max,
2026-10-07). After each screen the job moves
its own next run to the end of that hold; a failed send waits one dwell and then tries the
next screen. A restart begins again at Today. `screen_seconds` under 3 or a bad host logs
`device_rotation not registered: bad [device] config` and the rest of the service runs.

Measured on the panel (2026-10-04): it takes a request in at about 12 KB/s, so a still is
1.4 s to send and a 4-page Books 5.9 s; an animation of n frames takes 1.45 n seconds, with
the loading cycle showing throughout (hence the celebrations as stills, above). Each command times out after 5 s per phase and 10 s overall; a clip gets
4 s per frame. A refused connection is retried twice. The adapter sends
`Draw/ResetHttpGifId` before its first clip and every 32 clips after.

Measured on the panel by direct probe (2026-10-07, five sends of one 64-pixel still per
variant, while the live service kept sending; `Draw/SendHttpGif`, PicNum 1; the probe is a
launchd job as above, built from `scratchpad/probe_upload.py` of that session): no transport
uploads a frame faster than the adapter, so the adapter is unchanged.

| variant | median | min | max |
|---|---|---|---|
| a. adapter as is (httpx, `Connection: close`), 3 runs | 1.41 / 1.42 / 1.56 s | 1.38 s | 2.78 s |
| b. raw socket, headers and body in one `sendall`, close | 1.66 s | 1.59 s | 3.53 s |
| c. b plus `TCP_NODELAY` | 2.60 s (contended run; min 1.61) | 1.61 s | 2.68 s |
| d. kept-alive connection, sends 0.3 s apart | 1st 2.76 s ok; 2nd empty reply; 3rd broken pipe | | |
| e. `Transfer-Encoding: chunked` body | 2.73 s | 2.59 s | 3.48 s |

The panel answers every request with `Connection: close` and closes the socket itself
(Libuhttpd 3.8.0), so keep-alive cannot carry a second send. The maxima are the live
service's sends colliding with the probe's (the panel also refuses or resets a connect
while it is busy with another; the adapter's connect retry covers that).

Measured on the panel by direct probe (2026-10-06):
- An upload costs about 0.17 s plus 78 ms per KB of body: a 64-pixel frame (16.4 KB) 1.45 s,
  a 32-pixel frame (`PicWidth` 32, 4.1 KB) 0.49 s. The 32-pixel animation was accepted
  (ReturnCode 0 on all 8 frames); whether it displays, and at what size, is unseen.
- `Device/PlayTFGif` (FileType 2) replies ReturnCode 0 at once and the panel then fetches the
  URL once with `DivoomApp/1.0 (compatible; curl/7.68.0)`. What it shows afterwards is its
  cloud channel (the "HOT" heart), not the GIF: seen by Max with Pillow-written GIFs and
  again with GIFs in the form of Divoom's own sample (`app/render/adapters/panelgif.py`).
  `[device] fetch_clips` therefore stays false.
- An unknown command is ReturnCode 1, "Only accept JSON parameters"; `Device/PlayGif` (the
  Times Gate command) is unknown in every parameter form tried.
- ReturnCode 0 does not mean done: `Draw/UseHTTPCommandSource` returns 0 and never fetches
  its `CommandUrl`; `Draw/CommandList` returns 0 and does not run a `Device/PlayTFGif` nested
  in it; `Channel/GetAllConf`, `Device/GetDeviceTime` and `Draw/GetHttpGifId` return 0 with
  no fields. `Channel/GetIndex` answers 1 while uploaded frames are showing;
  `Channel/SetIndex` 3 is accepted and read back as 3 (the panel was put back to 1).
- Watched by Max on the evening of 2026-10-06: a fetched GIF left alone for 22 s in the
  cloud channel, or in the custom channel (`Channel/SetIndex` 3), is never shown; the panel
  shows the heart for 10 to 15 s and then plays its own cloud gallery (a hatching egg).
  Divoom's own sample URL: the heart only. A fetched GIF is therefore not a delivery route
  on this firmware. The 32-pixel 8-frame upload (3.6 s) displayed as an animation after a
  loading cycle; an uploaded still displays with no loading cycle at all.

When something fails (device off, timeout, one screen's renderer raising): one
`job device_rotation failed: ...` line per distinct error in the err log (repeats are not
logged), nothing is sent on that tick, and the next tick tries the next screen.

The week-complete party clip exists but is not in the rotation.

Still unverified on the panel:
- that a sparkle animates at its 70 ms frame time and Books pages at 2 s (PicSpeed);
- that it keeps showing the last clip while the service is down ("never blanks");
- the reset interval of 32 (a community figure for the older API);
- brightness, gamma and legibility over a full day (Phase 5).

## 16. Upgrading (new code, new migration)

The running service never migrates: it reads its list of migrations once, when it
starts, and every request and job refuses a db whose schema version is not the
one it started with (the phone gets 503 `schema_mismatch`, the pushed file is
kept in `data/raw/health/` and the next push re-sends those days). The tools
that read the live db (`tools.replay`, `tools.sync`, `tools.render --date`,
`tools.backup --verify`) do not migrate either; on a db that is behind they
stop with `refusing: ... run uv run python -m tools.migrate` and exit 2.
`tools.backup` itself works on a db that is behind, so the backup comes first.

```sh
cd /Users/maxwelllee12/life-dashboard
git pull && uv sync
uv run python -m tools.backup                                   # 1. back up (works before migrating)
launchctl bootout gui/$(id -u)/com.maxlee.life-dashboard        # 2. stop
uv run python -m tools.migrate                                  # 3. migrate; prints schema N -> M and "normalised K activity record(s)"
uv run python -m tools.replay --verify                          # 4. stored data still equals the archive; exit 0
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.maxlee.life-dashboard.plist   # 5. start
curl -s http://127.0.0.1:8080/healthz
```

When a parser changed and live still holds what the old one stored (`--verify` in step 4
prints differences that the code change explains), rebuild the live data tables from the
archive before starting the service:

```sh
uv run python -m tools.replay --rebuild-live                    # every parsed payload, in arrival order
uv run python -m tools.replay --rebuild-live --drop-ids 1,2,3   # ... leaving out test pushes for good
uv run python -m tools.replay --verify                          # 0 difference(s)
```

It replaces the ingested tables in one transaction, keeps the summary lines, recomputes the
metrics, and moves any dropped payload's file to `data/raw/_dropped/` (never deleted). The
backup from step 1 is the way back.

`tools.migrate` is safe to repeat. Besides the schema it re-clusters every
stored workout copy into activities by the current rule (notes.txt § Dedupe:
clustering owns activities) and rewrites each activity from its canonical copy;
run it once after pulling a commit that changes that rule (the issue #4 commit,
migration 005, is one), or `tools.replay --verify` reports those activities as
differences. After migration 005 the metrics engine ignores withdrawn workouts
(deleted in Health); nothing else is asked of the operator. If step 3 or 4 fails: do not start the service; restore the
backup from step 1 (section 12, `--overwrite-live`) and check out the previous
commit. Starting the service without step 3 also migrates the schema (it does
so at start), but it does not normalise old activities and gives you no chance
to look first.

## 17. Goodreads

The `read` shelf's public RSS is the reading source. Its URL carries a private
key, so it is the secret `GOODREADS_RSS_URL` in `.env` (Goodreads → My Books →
the `read` shelf → the RSS icon at the bottom of the list; copy the link). Set
it, restart the service (section 4), and `goodreads_poll` is registered; leave
it empty and the job is simply absent, nothing else changes. The host must be
`www.goodreads.com` or `goodreads.com` over https; any other URL, any redirect
(even within Goodreads), a non-2xx status or a body over 32 MB is refused with
nothing archived, and the failure shows in the job stats and the err log.

```sh
uv run python -m tools.sync --source goodreads      # poll by hand: "goodreads ok raw_archive_id=N books=75",
                                                    # "goodreads duplicate ..." (shelf unchanged), exit 2 + message when the URL is unset
ls data/raw/goodreads | tail                        # archived feeds, <utc stamp>_<sha8>.xml, verbatim bytes
sqlite3 data/life.db "SELECT id, title, read_at, date_added, date_inferred FROM books ORDER BY read_at DESC LIMIT 10"
grep -n "goodreads_poll" data/logs/*.err.log | tail # a refused redirect, a timeout, a malformed feed
```

What the archive holds: the exact bytes Goodreads served, one file per distinct
feed. An unchanged shelf serves identical bytes (`lastBuildDate` is the time of
the last shelf change, not of the fetch), so a daily poll of an unchanged shelf
adds no file and changes nothing. A feed that will not parse is archived with
`parsed_ok = 0` and the error in `raw_archive.error`; `tools.replay --verify`
lists it as unparsed and does not replay it.

What lands in `books`: one row per item keyed by Goodreads' book id; title,
author, `read_at`, `date_added`, `date_inferred`. The feed is authoritative for
what it carries, so a read-at date fixed on Goodreads replaces the stored one at
the next poll; a book taken off the shelf keeps its row. `read_at` is the chosen
calendar day's midnight America/New_York stored as UTC (`2026-02-17T05:00:00Z`
for 17 Feb); `date_added` is the instant Goodreads recorded. With
`books.fallback_to_date_added = true` a book with no read-at date takes its
date-added as `read_at` and is marked `date_inferred = 1` (Phase 3 shows the
mark); set the key to `false` and `read_at` stays null for those. A date in no
accepted format is stored as null and logged with the book id; it is never
guessed. Accepted: the RFC 822 form Goodreads writes (`Tue, 17 Feb 2026
00:00:00 +0000`, day padded or not, weekday optional, seconds optional, numeric
offset or GMT/UTC or a US zone name) and ISO 8601.

Catch-up: when the service starts and no parsed feed arrived in the last 24 h
(first boot, or the Mac slept through 06:30 for longer than the 18 h misfire
grace) the job runs once three minutes after start, then settles on 06:30.
Because an unchanged shelf adds no raw row, a restart more than a day after the
last shelf change also triggers that one poll; it costs one 300 KB fetch.

## 18. Summary (the daily line)

The `daily_summary` job runs at `summary.time` (06:50 home time) and writes one
sentence about yesterday, the latest complete day (health pushes cover whole days
ending yesterday), into today's `daily_metrics.summary_device_line`, which the
device shows all day. `--date D` means "the line shown on D" and describes D - 1
(plus an optional
`summary_web_line` and `summary_source`: `model` or `fallback`). Health data
stays home: the model receives daily aggregates only (the payload is the exact
set of numbers it may use), never raw samples or sub-day timestamps, and a test
greps the serialized request for both.

```sh
uv run python -m tools.summary --date 2026-10-02 --dry-run   # exact request body + cap check; calls nothing, writes nothing
uv run python -m tools.summary --date 2026-10-02             # write the day (stored line → no call)
uv run python -m tools.summary --date 2026-10-02 --force     # regenerate, calling the model again
uv run python -m tools.summary --spend                       # month-to-date usd, calls, cap
sqlite3 data/life.db "SELECT day_local, source, gate_result, line FROM summary_lines ORDER BY day_local DESC LIMIT 7"
sqlite3 data/life.db "SELECT day_local, request_id, input_tokens, cache_read_tokens, output_tokens, usd, stop_reason FROM model_spend ORDER BY id DESC LIMIT 7"
grep -n "summary .* model unavailable" data/logs/life-dashboard.err.log | tail   # why a day fell back
```

Model: `summary.model` in `config.toml` (`claude-opus-5-5`), key `ANTHROPIC_API_KEY`
in `.env`. No key → every day is rule-based copy and no call is attempted. Cap:
`summary.monthly_cap_usd` (3). Before each call, month-to-date plus the worst
case for that call (about $0.022) must stay under the cap; otherwise the day
falls back and the attempt is recorded as `cap: ...` in `summary_lines.attempts_json`.
Read `--spend` before and after any round that calls the model.

`tools.replay --verify` never compares the three `summary_*` keys: they are
authored output, not a function of the raw archive. `--snapshot`/`--diff` keep
them.

A line the gate rejects (invented number, ban list, a named source twice in a
week, too similar to a recent line) is regenerated once with the reason, then
replaced by rule-based copy. The gate's rules and the mutants that prove them
are in `app/summary/gate.py` and `tests/summary/test_mutants.py`.
## 19. Metrics (the engine behind the dots, streak, wins and load)

`app/metrics/` turns the stored tables into `daily_metrics` and `weekly_metrics`
rows; every rule is in `notes.txt § Goal model` and the row "Metrics engine" under
§ Architecture assumptions. It runs on the schedule above and never on a push,
so a number on the frame is at most `metrics.recompute_minutes` behind the data.

```sh
uv run python -m tools.metrics --recompute                 # recompute everything through today, now
uv run python -m tools.metrics --recompute --today 2026-10-05   # as of the end of that day (for a look back)
uv run python -m tools.metrics --show 2026-10-05           # the day's row and its week's row as JSON
uv run python -m tools.metrics --history                   # every load-bar change, then the placeholder
grep -n "metrics recomputed" data/logs/life-dashboard.out.log | tail   # the job logs only when it wrote something
```

What to expect:
- A week's `week_hit` stays `null` from Monday 00:00 until the first Workouts push
  after it (a Health Metrics push does not count), or 12 h with no such push
  (`metrics.week_close_grace_hours`), so a Sunday-night
  workout in Monday's 06:00 push still counts and the streak does not flicker to 0
  overnight. The device shows the streak as it stood until then.
- The load bar is the placeholder 100 until three runs of 4 miles or more with
  heart-rate samples exist; `--history` then shows the decision with the three run
  ids and the Monday it applies from. Past weeks keep the bar they were scored
  under; nothing before that Monday changes.
- `tools.replay --verify` also checks these rows: it recomputes the replay under
  the clock of the last recompute and compares. If a push landed after that
  recompute it prints `note: metrics: derived rows not compared ...` and compares
  the ingested tables only; run `tools.metrics --recompute` and verify again.
- A recompute that fails writes nothing (one transaction) and the job logs
  `job metrics_recompute failed`; the previous rows stay on the frame.
