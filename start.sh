#!/usr/bin/env bash
# Start the Hermes Operate dashboard server.
cd "$(dirname "$0")"
PORT="${1:-8790}"
exec python3 server.py --port "$PORT"
