#!/usr/bin/env bash
# Install (or reinstall) the life-dashboard LaunchAgent for the current user.
# Idempotent: safe to run again after editing the plist.
set -euo pipefail

LABEL="com.maxlee.life-dashboard"
REPO="/Users/maxwelllee12/life-dashboard"
SRC="$REPO/additional/launchd/$LABEL.plist"
DEST="$HOME/Library/LaunchAgents/$LABEL.plist"
DOMAIN="gui/$(id -u)"

[ -f "$SRC" ] || { echo "missing $SRC" >&2; exit 1; }
[ -x "/Users/maxwelllee12/.local/bin/uv" ] || { echo "uv not found at /Users/maxwelllee12/.local/bin/uv; fix ProgramArguments in the plist" >&2; exit 1; }

plutil -lint "$SRC"

mkdir -p "$REPO/data/logs" "$HOME/Library/LaunchAgents"
cp "$SRC" "$DEST"

if launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1; then
  echo "unloading existing $LABEL"
  launchctl bootout "$DOMAIN/$LABEL"
fi

launchctl bootstrap "$DOMAIN" "$DEST"
launchctl kickstart -k "$DOMAIN/$LABEL"

sleep 2
echo "--- launchctl print $DOMAIN/$LABEL ---"
launchctl print "$DOMAIN/$LABEL" | head -20
echo "--- listening? ---"
lsof -nP -iTCP:8080 -sTCP:LISTEN || echo "nothing on :8080 yet; tail $REPO/data/logs/life-dashboard.err.log"
