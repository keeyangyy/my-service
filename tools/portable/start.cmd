@echo off
rem my-service portable launcher (stable entry: survives app/ upgrades)
setlocal
set "HOME=%~dp0"
if "%HOME:~-1%"=="\" set "HOME=%HOME:~0,-1%"

if exist "%HOME%\runtime\pythonw.exe" (
  start "" /min /d "%HOME%\app\src" "%HOME%\runtime\pythonw.exe" "%HOME%\app\src\server.py"
  exit /b 0
)
if exist "%HOME%\runtime\python.exe" (
  start "" /min /d "%HOME%\app\src" "%HOME%\runtime\python.exe" "%HOME%\app\src\server.py"
  exit /b 0
)
where pythonw >nul 2>nul
if %errorlevel%==0 (
  start "" /min /d "%HOME%\app\src" pythonw "%HOME%\app\src\server.py"
  exit /b 0
)
start "" /min /d "%HOME%\app\src" python "%HOME%\app\src\server.py"
exit /b 0