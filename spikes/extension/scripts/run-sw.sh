#!/usr/bin/env bash
# Spike 3 runner: start the WS server and a bare Chromium (Playwright's
# "Chrome for Testing" build) with ONLY the extension loaded — no extension
# pages open, and no CDP/DevTools attached (an attached debugger keeps service
# workers alive and would invalidate the test).
#
# Usage: scripts/run-sw.sh <name> <build-dir-suffix> <server-mode> <port> <minutes> [headless|headed]
#   scripts/run-sw.sh main    ""        app      8765 30
#   scripts/run-sw.sh none    -wsnone   none     8766 3
#   scripts/run-sw.sh proto   -wsproto  protocol 8767 3
set -euo pipefail
cd "$(dirname "$0")/.."
NAME=$1 SUFFIX=$2 MODE=$3 PORT=$4 MINUTES=$5 HEAD=${6:-headless}
RUN=runs/$NAME
rm -rf "$RUN"; mkdir -p "$RUN" logs
cp -R ".output/chrome-mv3$SUFFIX" "$RUN/ext"
CHROME=$(node -e 'console.log(require("playwright").chromium.executablePath())')

(cd ws-server && exec /opt/homebrew/bin/uv run server.py --port "$PORT" --mode "$MODE" --interval 20 --log "../logs/$NAME.jsonl") >"$RUN/server.out" 2>&1 &
SERVER=$!
sleep 2

HEADLESS_FLAG=()
[ "$HEAD" = headless ] && HEADLESS_FLAG=(--headless=new)
"$CHROME" "${HEADLESS_FLAG[@]}" \
  --user-data-dir="$PWD/$RUN/profile" \
  --no-first-run --no-default-browser-check --disable-default-apps \
  --disable-extensions-except="$PWD/$RUN/ext" --load-extension="$PWD/$RUN/ext" \
  about:blank >"$RUN/chrome.out" 2>&1 &
CHROME_PID=$!
# Control client + process-state sampler (detects server/browser stalls, App Nap, sleep).
(cd ws-server && exec /opt/homebrew/bin/uv run probe.py "$PORT") >"$RUN/probe.out" 2>&1 &
PROBE=$!
( while true; do echo "$(date -u +%T) $(ps -o pid=,stat=,%cpu= -p "$CHROME_PID","$SERVER" | tr '\n' ' ')"; sleep 60; done ) >"$RUN/ps.out" 2>&1 &
SAMPLER=$!
echo "server=$SERVER chrome=$CHROME_PID run=$RUN log=logs/$NAME.jsonl start=$(date -u +%FT%TZ)"

sleep $((MINUTES * 60))
kill "$CHROME_PID" "$PROBE" "$SAMPLER" 2>/dev/null || true
sleep 2
kill "$SERVER" 2>/dev/null || true
wait 2>/dev/null || true
echo "done $(date -u +%FT%TZ)"
node scripts/summarise-ws.mjs "logs/$NAME.jsonl" | tee "$RUN/summary.txt"
