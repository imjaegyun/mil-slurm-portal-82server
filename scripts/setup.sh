#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$ROOT/.env"
ENV_EXAMPLE="$ROOT/.env.example"

if [[ -f "$ENV_FILE" ]]; then
  set -a
  source "$ENV_FILE"
  set +a
  echo "Existing configuration preserved: $ENV_FILE"
  echo "Remote portal port: ${PORTAL_PORT:-18765}"
else
  if [[ ! -r "$ENV_EXAMPLE" ]]; then
    echo "Missing configuration template: $ENV_EXAMPLE" >&2
    exit 1
  fi

  uid_value="$(id -u)"
  port_base="${PORTAL_PORT_BASE:-20000}"
  if [[ ! "$port_base" =~ ^[0-9]+$ ]] || ((port_base < 1024 || port_base > 35000)); then
    echo "PORTAL_PORT_BASE must be between 1024 and 35000." >&2
    exit 1
  fi
  candidate=$((port_base + uid_value % 30000))
  selected_port=""
  if [[ "${PORTAL_SKIP_PORT_CHECK:-0}" == "1" ]]; then
    selected_port="$candidate"
  else
    for ((offset = 0; offset < 200; offset += 1)); do
      port=$((candidate + offset))
      if ((port > 65535)); then
        break
      fi
      if python3 -c \
        'import socket, sys
s = socket.socket()
try:
    s.bind(("127.0.0.1", int(sys.argv[1])))
except OSError:
    raise SystemExit(1)
finally:
    s.close()' \
        "$port"; then
        selected_port="$port"
        break
      fi
    done
  fi
  if [[ -z "$selected_port" ]]; then
    echo "Could not find an available loopback port." >&2
    exit 1
  fi

  umask 077
  awk -v port="$selected_port" '
    /^PORTAL_PORT=/ {
      print "PORTAL_PORT=" port
      found = 1
      next
    }
    { print }
    END {
      if (!found) {
        print "PORTAL_PORT=" port
      }
    }
  ' "$ENV_EXAMPLE" > "$ENV_FILE"
  chmod 600 "$ENV_FILE"
  echo "Created per-user configuration: $ENV_FILE"
  echo "Remote portal port: $selected_port"
fi

if [[ "${PORTAL_SKIP_CLI_LINK:-0}" != "1" ]]; then
  cli_dir="${HOME}/.local/bin"
  cli_link="$cli_dir/mil-jobs"
  mkdir -p "$cli_dir"
  if [[ -e "$cli_link" && ! -L "$cli_link" ]]; then
    echo "Existing non-symlink mil-jobs was not replaced: $cli_link" >&2
  else
    ln -sfn "$ROOT/scripts/mil-jobs" "$cli_link"
    echo "Installed mil-jobs: $cli_link"
  fi
fi

echo
echo "Next:"
echo "  cd \"$ROOT\""
echo "  ./scripts/start.sh"
echo "  ./scripts/access.sh"
