#!/usr/bin/env bash
# Hermes Watchtower launcher: start the server if it is not already running,
# then open the UI in a browser.
#
# Usage: ./open.sh [port]
set -u
cd "$(dirname "$0")" || exit 1

PORT="${1:-8790}"
URL="http://127.0.0.1:${PORT}"

probe() { curl -fs -o /dev/null --max-time 1 "${1}/api/data?range=7" 2>/dev/null; }

open_url() {
  local u="$1" c
  for c in "${BROWSER:-}" xdg-open open wslview; do
    [ -n "$c" ] || continue
    if command -v "$c" >/dev/null 2>&1; then
      "$c" "$u" >/dev/null 2>&1 &
      return 0
    fi
  done
  return 1
}

if ! probe "$URL"; then
  (nohup python3 server.py --port "$PORT" > server.log 2>&1 &)
  i=0
  while [ "$i" -lt 30 ]; do
    sleep 0.2
    probe "$URL" && break
    i=$((i + 1))
  done
fi

# If the port was taken, the server falls back to an ephemeral one and logs it.
if ! probe "$URL" && [ -f server.log ]; then
  ALT="$(grep -o 'http://127\.0\.0\.1:[0-9]\+' server.log | tail -1)"
  if [ -n "$ALT" ] && probe "$ALT"; then URL="$ALT"; fi
fi

if ! probe "$URL"; then
  echo "Hermes Watchtower failed to start — see $(pwd)/server.log" >&2
  exit 1
fi

echo "$URL"
open_url "$URL" || echo "Open $URL in your browser." >&2
