@echo off
rem Calepin (Windows): double-click to start the app and open its page in the browser.
rem If it is already running, the page simply opens again. Close this window (or Ctrl+C) to stop it.
cd /d "%~dp0"
set PYTHONUTF8=1
where py >nul 2>nul && (py -3 pipeline\revue.py %* & goto :eof)
where python >nul 2>nul && (python pipeline\revue.py %* & goto :eof)
echo Python 3.9+ is required: https://www.python.org/downloads/
pause
