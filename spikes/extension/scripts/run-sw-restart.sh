#!/usr/bin/env bash
# Spike 3 extra: server ("daemon") goes away for 60 s and comes back.
# Uses the wsnone build (port 8766) but runs the server in app-ping mode.
set -euo pipefail
cd "$(dirname "$0")/.."
RUN=runs/restart; LOG=logs/restart.jsonl
rm -rf "$RUN" "$LOG"; mkdir -p "$RUN" logs
cp -R .output/chrome-mv3-wsnone "$RUN/ext"
CHROME=$(node -e 'console.log(require("playwright").chromium.executablePath())')
srv() { (cd ws-server && exec /opt/homebrew/bin/uv run server.py --port 8766 --mode app --interval 20 --log "../$LOG") >>"$RUN/server.out" 2>&1 & echo $!; }
S=$(srv); sleep 2
"$CHROME" --headless=new --user-data-dir="$PWD/$RUN/profile" --no-first-run --no-default-browser-check \
  --disable-extensions-except="$PWD/$RUN/ext" --load-extension="$PWD/$RUN/ext" about:blank >"$RUN/chrome.out" 2>&1 &
C=$!
echo "up $(date -u +%T)"; sleep 60
kill $S; echo "server down $(date -u +%T)"; sleep 60
S=$(srv); echo "server up $(date -u +%T)"; sleep 120
kill $C; sleep 2; kill $S; wait 2>/dev/null || true
node scripts/summarise-ws.mjs "$LOG"
