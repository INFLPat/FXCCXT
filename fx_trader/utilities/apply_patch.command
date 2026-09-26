#!/bin/bash
# apply_patch.command
#
# Double-click to run. If a JSON file was dropped onto this file's icon
# (or passed as an argument), uses it directly. Otherwise shows a native
# macOS "choose file" dialog - reliable on every macOS version, unlike
# raw drag-and-drop onto a plain script, which Finder does not always
# treat as a valid drop target (see fx_trader/utilities/README.md).
#
# One-time setup: chmod +x apply_patch.command

set -euo pipefail
cd "$(dirname "$0")/../.."   # repo root (this file lives in fx_trader/utilities/)

if [ "$#" -ge 1 ]; then
  PAYLOAD="$1"
else
  PAYLOAD=$(osascript -e 'POSIX path of (choose file with prompt "Choose the session patch JSON" of type {"json"})')
fi

echo "Using payload: $PAYLOAD"
python3 fx_trader/utilities/apply_patch.py "$PAYLOAD"
echo ""
read -p "Press Enter to close..."
