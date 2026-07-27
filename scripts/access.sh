#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ ! -r "$ROOT/.env" ]]; then
  echo "Run $ROOT/scripts/setup.sh first." >&2
  exit 1
fi

set -a
source "$ROOT/.env"
set +a

remote_port="${PORTAL_PORT:-18765}"
ssh_host="${1:-82server}"
local_port="${2:-18765}"
if [[ ! "$remote_port" =~ ^[0-9]+$ ]] || [[ ! "$local_port" =~ ^[0-9]+$ ]]; then
  echo "Portal ports must be numeric." >&2
  exit 1
fi

echo "Personal MIL Compute Portal"
echo "  Project:     $ROOT"
echo "  Remote port: $remote_port"
echo "  Local URL:   http://127.0.0.1:$local_port"
echo
echo "1. On the server, get your personal token:"
echo "   cd \"$ROOT\" && ./scripts/token.sh"
echo
echo "2. On your own computer, keep this tunnel running."
echo "   The same command works in macOS/Linux Terminal and Windows PowerShell/CMD:"
echo "   ssh -N -L 127.0.0.1:${local_port}:127.0.0.1:${remote_port} ${ssh_host}"
echo
echo "3. Open http://127.0.0.1:${local_port}"
echo
echo "SSH host aliases are read automatically from:"
echo "  macOS/Linux: ~/.ssh/config"
echo "  Windows:     %USERPROFILE%\\.ssh\\config"
echo "If no alias is configured, pass user@server-address instead of ${ssh_host}."
