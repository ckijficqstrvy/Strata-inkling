#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
PY="$ROOT/control/python/bin/python3"
if [[ ! -x "$PY" ]]; then
  echo "Environment not installed yet. Run ./install-mac.sh first."
  exit 1
fi
exec "$PY" "$ROOT/gui/app.py" --launch
