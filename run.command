#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

pick_python() {
  for candidate in python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
      echo "$candidate"
      return 0
    fi
  done
  return 1
}

if ! PYTHON_BIN="$(pick_python)"; then
  echo "Python is not installed. Install Python 3.10-3.13 first."
  echo "https://www.python.org/downloads/"
  exit 1
fi

PY_VERSION="$("$PYTHON_BIN" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
case "$PY_VERSION" in
  3.10|3.11|3.12|3.13) ;;
  *)
    echo "Unsupported Python version: $PY_VERSION"
    echo "Please use Python 3.10, 3.11, 3.12, or 3.13."
    exit 1
    ;;
esac

echo "Using $("$PYTHON_BIN" --version)"

if [ ! -d ".venv" ]; then
  "$PYTHON_BIN" -m venv .venv
fi

# shellcheck disable=SC1091
source .venv/bin/activate

python -m pip install --upgrade pip
python -m pip install -r requirements.txt

# Best effort: raise soft nofile to hard limit so large sellers do not exhaust FDs.
HARD_LIMIT="$(ulimit -Hn 2>/dev/null || true)"
if [ -n "${HARD_LIMIT:-}" ] && [ "$HARD_LIMIT" != "unlimited" ]; then
  ulimit -n "$HARD_LIMIT" 2>/dev/null || true
fi

APP_HOST="${APP_HOST:-127.0.0.1}"
APP_PORT="${APP_PORT:-5080}"

echo "Open file limit: $(ulimit -Sn 2>/dev/null || echo "unknown")"
echo "Starting app at http://${APP_HOST}:${APP_PORT}"
exec flask --app app run --host "$APP_HOST" --port "$APP_PORT"
