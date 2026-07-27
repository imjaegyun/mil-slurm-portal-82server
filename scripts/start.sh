#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE="$ROOT/.state"
PID_FILE="$STATE/server.pid"
LOG_FILE="$STATE/server.log"

if [[ ! -f "$ROOT/.env" ]]; then
  "$ROOT/scripts/setup.sh"
fi

if [[ -f "$ROOT/.env" ]]; then
  set -a
  source "$ROOT/.env"
  set +a
fi
PORTAL_PORT="${PORTAL_PORT:-18765}"

umask 077
mkdir -p "$STATE"

if [[ -f "$PID_FILE" ]]; then
  IFS= read -r existing_pid < "$PID_FILE"
  if [[ "$existing_pid" =~ ^[0-9]+$ ]] && kill -0 "$existing_pid" 2>/dev/null; then
    echo "MIL Compute Portal is already running (PID $existing_pid)."
    exit 0
  fi
fi

nohup python3 "$ROOT/app.py" --host 127.0.0.1 --port "$PORTAL_PORT" >> "$LOG_FILE" 2>&1 &
portal_pid=$!
echo "$portal_pid" > "$PID_FILE"
sleep 1

if ! kill -0 "$portal_pid" 2>/dev/null; then
  echo "Portal failed to start. Check $LOG_FILE" >&2
  exit 1
fi

echo "MIL Compute Portal started on 127.0.0.1:$PORTAL_PORT (PID $portal_pid)."
echo "Run $ROOT/scripts/access.sh 82server for your tunnel command."
