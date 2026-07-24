#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PID_FILE="$ROOT/.state/server.pid"
if [[ -f "$ROOT/.env" ]]; then
  set -a
  source "$ROOT/.env"
  set +a
fi
PORTAL_PORT="${PORTAL_PORT:-18765}"

if [[ -f "$PID_FILE" ]]; then
  IFS= read -r portal_pid < "$PID_FILE"
  if [[ "$portal_pid" =~ ^[0-9]+$ ]] && kill -0 "$portal_pid" 2>/dev/null; then
    echo "running pid=$portal_pid url=http://127.0.0.1:$PORTAL_PORT"
    exit 0
  fi
fi

echo "stopped"
exit 1
