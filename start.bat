@echo off
rem SignalFlow demo for Windows: double-click or run "start.bat" (extra args go to run_demo.py,
rem e.g. "start.bat --mock" or "start.bat --check"). Needs Python 3.11+ and Node.js 20+.
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo [start] creating virtual environment .venv ...
  where py >nul 2>nul && (py -3 -m venv .venv) || (python -m venv .venv)
  if errorlevel 1 goto :nopython
)
if not exist ".venv\Scripts\python.exe" goto :nopython

".venv\Scripts\python.exe" -c "import fastapi, uvicorn, numpy, networkx" >nul 2>nul
if errorlevel 1 (
  echo [start] installing Python packages ...
  ".venv\Scripts\python.exe" -m pip install -q -r backend\requirements.txt
  if errorlevel 1 goto :fail
)

".venv\Scripts\python.exe" run_demo.py %*
exit /b %errorlevel%

:nopython
echo [start] Python 3.11+ was not found. Install it (winget install Python.Python.3.12), then run start.bat again.
exit /b 1

:fail
echo [start] setup failed, see the messages above.
exit /b 1
