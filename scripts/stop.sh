#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PID_FILE="$ROOT/.state/server.pid"

if [[ ! -f "$PID_FILE" ]]; then
  echo "Portal is not running."
  exit 0
fi

IFS= read -r portal_pid < "$PID_FILE"
if [[ ! "$portal_pid" =~ ^[0-9]+$ ]] || [[ ! -r "/proc/$portal_pid/cmdline" ]]; then
  echo "Stale PID file found; no process was stopped." >&2
  exit 1
fi

process_command="$(tr '\0' ' ' < "/proc/$portal_pid/cmdline")"
if [[ "$process_command" != *"$ROOT/app.py"* ]]; then
  echo "PID $portal_pid does not belong to this portal; refusing to stop it." >&2
  exit 1
fi

kill "$portal_pid"
for _ in 1 2 3 4 5; do
  if ! kill -0 "$portal_pid" 2>/dev/null; then
    break
  fi
  sleep 1
done

mv "$PID_FILE" "$PID_FILE.stopped"
  echo "MIL Compute Portal stopped."
