#!/bin/sh
set -eu

USER_HOME=$(python3 -c 'from pathlib import Path; print(Path.home())')
USER_ID=$(id -u)
LABEL="com.mxmsmnv.codex-telegram-bridge"
PLIST_PATH="$USER_HOME/Library/LaunchAgents/$LABEL.plist"

if launchctl print "gui/$USER_ID/$LABEL" >/dev/null 2>&1; then
  launchctl bootout "gui/$USER_ID/$LABEL"
fi

if [ -f "$PLIST_PATH" ]; then
  rm "$PLIST_PATH"
fi

echo "Removed $LABEL. Keychain credentials and runtime logs were preserved."
