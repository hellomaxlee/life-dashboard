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
| `device_rotation` | every `device.screen_seconds` (20 s), **only while `device.pixoo_host` is set** | sends the next screen (Week, Today, Books, wrapping) to the Pixoo; section 15 |

The Goodreads 06:30 job is not here; it is added to `build_scheduler` together with the poller.

A job never overlaps itself, opens its own db connection per run, and an
exception is logged (`job <id> failed` plus traceback in the err log), not fatal.

```sh
grep -n "job .* failed" data/logs/life-dashboard.err.log | tail     # any job failure
ls -lt data/backups | head                                          # did last night's backup land
ls data/raw/claude_usage | wc -l                                    # usage readings archived
LIFE_SCHEDULER_ENABLED=0 uv run fastapi dev app/main.py --port 8080 # run with no jobs (debugging)
```

`LIFE_SCHEDULER_ENABLED` takes `1/true/yes` or `0/false/no`; anything else stops the
service at boot with a message naming the variable. A backup that found raw files
missing from the live archive still lands, and logs
`backup life-... is missing N raw file(s): ...` to the err log.

Mac asleep at 03:15: the backup runs within 30 s of the next wake if that is
inside 18 hours; otherwise that night is skipped. Service started with no
backup newer than 26 hours (first boot, long power-off): one backup runs two
minutes after start. Reading the usage file by hand still works:
`uv run python -m tools.sync --source claude_usage`.

## 11. Backup

Full detail: `workflows/backup.md`. Safe while the service is running.

```sh
cd /Users/maxwelllee12/life-dashboard
uv run python -m tools.backup                                  # what the 03:15 job does, now
uv run python -m tools.backup --verify "$(ls -d data/backups/life-* | tail -1)"   # exit 0 = identical
uv run python -m tools.backup --out /Volumes/SomeDrive/life-backup-2026-10-02     # extra copy, local path
```

`--verify` restores to a temp dir, diffs every data table against the live db,
and replays the backup's raw archive from scratch; `0 difference(s)` and exit 0
is the pass. A push that lands between the backup and the verify shows up as
`live vs backup: ... +` lines; take a new backup and verify that one.

## 12. Restore

Look first, into a scratch directory (never touches the live db):

```sh
uv run python -m tools.backup --restore data/backups/life-20261002T071500Z --to /tmp/life-restore
sqlite3 /tmp/life-restore/life.db "PRAGMA integrity_check; SELECT COUNT(*) FROM activities;"
```

Replace the live db (disaster only). Stop the service first, or launchd will
hold the old file open:

```sh
launchctl bootout gui/$(id -u)/com.maxlee.life-dashboard
uv run python -m tools.backup --restore data/backups/life-20261002T071500Z --to data --overwrite-live
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.maxlee.life-dashboard.plist
curl -s http://127.0.0.1:8080/healthz
```

Without `--overwrite-live` the command refuses and exits 2. Anything pushed
after the backup is refilled by the phone's next push (each carries 7 days).

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

What the states mean on the real box: a raw file with no `raw_archive` row is a
push that died before it was recorded; it is harmless and the phone's next push
re-sends those days. A row with `parsed_ok = 0` and no error is a push that died
mid-parse or failed; re-posting the same bytes parses it, unless a later push
of the same source has already been parsed, in which case the re-post answers
`superseded` and stores nothing (older data must not land on top of newer). A `raw_archive` row whose file is missing is the only
combination that is a bug; `tools.backup --verify` reports it.

After a real power cut or a hard reset (untested by the drill; these are the checks to run):

```sh
sqlite3 data/life.db "PRAGMA integrity_check;"            # must print ok
sqlite3 data/life.db "SELECT id, source, received_at_utc, error FROM raw_archive WHERE parsed_ok = 0;"
uv run python -m tools.backup && uv run python -m tools.backup --verify "$(ls -d data/backups/life-* | tail -1)"
```

## 14. Egress

`uv run pytest tests/test_egress.py -q` proves, statically, that nothing under
`app/` or `tools/` names a host outside `{www.goodreads.com, goodreads.com,
api.anthropic.com}` plus loopback and `192.168.x.x`, and that nothing imports
`requests` or `urllib.request`. The gate carries its own mutants
(`test_gate_catches_*`) so a scanner regression turns red, not green.

## 15. Display (Pixoo-64, not yet purchased)

Today `[device] pixoo_host` is empty: the `device_rotation` job is not registered, no
adapter is built, and nothing is sent anywhere. To turn the display on once it is bought:

1. Give the Pixoo a DHCP reservation in the router (as in section 7) and note its IP.
2. In `config.toml`: `pixoo_host = "192.168.1.50"` (the literal IP; a hostname or any
   address outside 10/8, 172.16/12, 192.168/16 is refused). Leave `screen_seconds = 20`.
3. `launchctl kickstart -k gui/$(id -u)/com.maxlee.life-dashboard`

```sh
grep -n "device_rotation" data/logs/life-dashboard.err.log | tail   # failures and refusals
```

What it does: the first screen goes out at start, then one screen every `screen_seconds`,
Week, Today, Books, wrapping, each rendered for today's America/New_York day from a fresh
db connection. A restart begins again at Week. A clip longer than the dwell (Books pages
are 2 s each, about 14 s at most) keeps its screen until it has played once; that tick logs
nothing and sends nothing. `screen_seconds` under 15 or a bad host logs
`device_rotation not registered: bad [device] config` and the rest of the service runs.

When something fails (device off, timeout, refused clip, one screen's renderer raising):
`job device_rotation failed` plus a traceback in the err log, nothing is sent on that tick,
and the next tick tries the next screen. Each command to the device times out after 2 s
and a whole clip gets half the dwell (10 s); a send that runs past the next tick makes the
scheduler skip that tick, never stack a second one.

No celebrations: the sparkle and party clips exist, but their triggers (a daily win, a
week hit) need the metrics engine, which does not exist yet. The rotation never plays them.

Unverified on hardware (no device is owned; every test uses a fake transport):
- the whole local API in `app/render/adapters/pixoo.py` (commands, 59-frame limit, PicSpeed);
- that the device loops a clip and keeps showing the last one when a tick fails or the
  service is down ("never blanks" rests on this);
- what the device shows when a send is cut off part-way by the 10 s budget;
- how long a real 7-page Books send takes, and so whether 2 s / 10 s are the right limits;
- brightness, gamma and legibility on the panel (Phase 5).
