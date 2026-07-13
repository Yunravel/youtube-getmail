@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo [1/3] Creating local Python environment...
  python -m venv .venv
  if errorlevel 1 py -3 -m venv .venv
  if errorlevel 1 goto :error
)

echo [2/3] Installing/updating dependencies...
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :error

echo [3/3] Starting application...
".venv\Scripts\python.exe" main.py
exit /b 0

:error
echo.
echo Startup failed. Install Python 3.10+ and try again.
pause
exit /b 1
