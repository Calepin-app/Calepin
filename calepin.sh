#!/bin/bash
# Calepin (Linux): run ./calepin.sh (or double-click if your file manager allows it) to start the app and open its page in the browser.
# If it is already running, the page simply opens again. Close this window (or Ctrl+C) to stop it.
cd "$(dirname "$0")"
for PY in python3 /opt/homebrew/bin/python3 /usr/local/bin/python3 /Library/Frameworks/Python.framework/Versions/Current/bin/python3; do
  if command -v "$PY" >/dev/null 2>&1 && "$PY" -c 'import sys; sys.exit(sys.version_info < (3, 9))' 2>/dev/null; then
    exec "$PY" pipeline/revue.py "$@"
  fi
done
echo "Python 3.9+ is required: https://www.python.org/downloads/"
read -r -p "Press Enter to close…"
