#!/usr/bin/env bash
# Stop and remove the life-dashboard LaunchAgent. Idempotent. Leaves data/ alone.
set -euo pipefail

LABEL="com.maxlee.life-dashboard"
DEST="$HOME/Library/LaunchAgents/$LABEL.plist"
DOMAIN="gui/$(id -u)"

if launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1; then
  launchctl bootout "$DOMAIN/$LABEL"
  echo "unloaded $LABEL"
else
  echo "$LABEL was not loaded"
fi

if [ -f "$DEST" ]; then
  rm -f "$DEST"
  echo "removed $DEST"
else
  echo "no plist at $DEST"
fi

if launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1; then
  echo "still loaded?!" >&2
  exit 1
fi
echo "done; data/ and data/logs/ untouched"
