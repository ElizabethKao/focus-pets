#!/bin/bash
# Sets Peep up the first time, then starts her. Safe to run again anytime.
set -euo pipefail
cd "$(dirname "$0")"
HERE="$(pwd)"

if ! python3 --version >/dev/null 2>&1; then
  echo "Peep needs Python 3. If a popup appeared asking to install developer tools,"
  echo "click Install, wait for it to finish, then run: bash start.sh"
  exit 1
fi

if [ ! -f .venv/.installed ]; then
  echo "🐥 First time setup (about a minute)..."
  python3 -m venv .venv
  .venv/bin/pip install --upgrade pip -q
  .venv/bin/pip install -r requirements.txt -q
  touch .venv/.installed
fi

pkill -f "$HERE/peep.py" 2>/dev/null || true
nohup .venv/bin/python "$HERE/peep.py" "$@" > peep.log 2>&1 &

echo "🐥 Peep and Honey are on your screen! You can close Terminal."
if [ "${1:-}" = "--test" ]; then
  echo "   TEST MODE. Watch decisions live with:  tail -f peep.log"
fi
echo "   Click the pet icon in your menu bar (or right-click a pet) for the menu."
