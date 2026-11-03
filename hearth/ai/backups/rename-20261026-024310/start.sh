#!/usr/bin/env bash
# Hearth on Linux/macOS: ./start.sh   (creates .venv on first run)
set -e
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
  grep -vE '^\s*(#|$)' requirements.txt | while read -r pkg; do .venv/bin/pip install -q "$pkg" || echo "skipped $pkg"; done
fi
exec .venv/bin/python server.py "$@"
