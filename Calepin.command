#!/bin/bash
# Calepin (macOS): double-click to start the app and open its page in the browser.
# If it is already running, the page simply opens again. Close this window (or Ctrl+C) to stop it.
cd "$(dirname "$0")"
for PY in python3 /opt/homebrew/bin/python3 /usr/local/bin/python3 /Library/Frameworks/Python.framework/Versions/Current/bin/python3; do
  if command -v "$PY" >/dev/null 2>&1 && "$PY" -c 'import sys; sys.exit(sys.version_info < (3, 9))' 2>/dev/null; then
    exec "$PY" pipeline/revue.py "$@"
  fi
done
echo "Calepin needs Python 3.9 or later."
echo "If macOS just offered to install the « command line developer tools », accept, wait for the end"
echo "of the installation, then double-click Calepin.command again. Otherwise: https://www.python.org/downloads/"
read -r -p "Press Enter to close…"
