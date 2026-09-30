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

## 10. Power-cut drill (manual for now; automation is a Phase 4 follow-up)

Claim: a `kill -9` mid-POST loses no committed data and leaves the metrics
table recomputable to an identical state. WAL mode makes the db survive; the
raw-before-parsed archive makes the metrics recomputable.

```sh
cd /Users/maxwelllee12/life-dashboard
# 0. baseline
uv run python -m tools.replay --since 2026-09-01
sqlite3 data/life.db ".dump daily_metrics weekly_metrics" | shasum -a 256 > /tmp/before.sha

# 1. kill mid-POST: start a slow POST of a fixture in one shell...
curl -s -X POST http://127.0.0.1:8080/ingest/health \
  -H 'Content-Type: application/json' \
  --data-binary @fixtures/health/train_day.json &
# ...and immediately kill the server in the same second
kill -9 "$(lsof -tnP -iTCP:8080 -sTCP:LISTEN)"

# 2. launchd relaunches it (KeepAlive); confirm
sleep 12; lsof -nP -iTCP:8080 -sTCP:LISTEN
sqlite3 data/life.db "PRAGMA integrity_check;"          # must print ok
ls data/life.db-wal data/life.db-shm 2>/dev/null         # WAL present is normal

# 3. replay twice and compare
uv run python -m tools.replay --since 2026-09-01
sqlite3 data/life.db ".dump daily_metrics weekly_metrics" | shasum -a 256 > /tmp/after1.sha
uv run python -m tools.replay --since 2026-09-01
sqlite3 data/life.db ".dump daily_metrics weekly_metrics" | shasum -a 256 > /tmp/after2.sha
diff /tmp/after1.sha /tmp/after2.sha && echo "replay idempotent"
```

Pass: `integrity_check` prints `ok`, `after1` equals `after2`, and re-POSTing the
interrupted fixture produces `after2` again. A partially written raw file (the
kill landed before the archive fsync) is acceptable only if it is also absent
from `raw_archive`; a raw row without a file, or a file without a row, is a bug.
Record the date and result in `CHANGELOG.md` when this is run for real.

## 11. Egress

`uv run pytest tests/test_egress.py -q` proves, statically, that nothing under
`app/` or `tools/` names a host outside `{www.goodreads.com, goodreads.com,
api.anthropic.com}` plus loopback and `192.168.x.x`, and that nothing imports
`requests` or `urllib.request`. The gate carries its own mutants
(`test_gate_catches_*`) so a scanner regression turns red, not green.
