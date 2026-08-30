#!/bin/sh
set -eu

if [ "$(uname -s)" != "Darwin" ]; then
  echo "This LaunchAgent installer supports macOS only." >&2
  exit 1
fi

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
BRIDGE_PATH="$SCRIPT_DIR/codex_telegram_bridge.py"
USER_HOME=$(python3 -c 'from pathlib import Path; print(Path.home())')
USER_NAME=$(id -un)
USER_ID=$(id -u)
LABEL="com.mxmsmnv.codex-telegram-bridge"
PLIST_PATH="$USER_HOME/Library/LaunchAgents/$LABEL.plist"
LOG_PATH="$USER_HOME/.codex/log/telegram-bridge-launchd.log"

mkdir -p "$USER_HOME/Library/LaunchAgents" "$USER_HOME/.codex/log"

if launchctl print "gui/$USER_ID/$LABEL" >/dev/null 2>&1; then
  launchctl bootout "gui/$USER_ID/$LABEL"
fi

python3 - "$PLIST_PATH" "$BRIDGE_PATH" "$USER_HOME" "$USER_NAME" "$LOG_PATH" <<'PY'
import plistlib
import sys
from pathlib import Path

plist_path, bridge_path, home, user, log_path = sys.argv[1:]
payload = {
    "Label": "com.mxmsmnv.codex-telegram-bridge",
    "ProgramArguments": [
        "/usr/bin/env",
        "-i",
        f"HOME={home}",
        f"USER={user}",
        f"LOGNAME={user}",
        "PATH=/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin",
        "/usr/bin/python3",
        bridge_path,
        "listen",
    ],
    "RunAtLoad": True,
    "KeepAlive": True,
    "ProcessType": "Background",
    "StandardOutPath": log_path,
    "StandardErrorPath": log_path,
}
with Path(plist_path).open("wb") as handle:
    plistlib.dump(payload, handle, sort_keys=False)
PY

plutil -lint "$PLIST_PATH"
launchctl bootstrap "gui/$USER_ID" "$PLIST_PATH"
launchctl kickstart -k "gui/$USER_ID/$LABEL"

echo "Installed and started: $LABEL"
echo "LaunchAgent: $PLIST_PATH"
echo "Log: $LOG_PATH"
