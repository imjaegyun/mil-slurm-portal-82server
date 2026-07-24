#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TOKEN_FILE="$ROOT/.state/access-token"

if [[ ! -r "$TOKEN_FILE" ]]; then
  echo "Token has not been generated. Start the portal first." >&2
  exit 1
fi

sed -n '1p' "$TOKEN_FILE"
