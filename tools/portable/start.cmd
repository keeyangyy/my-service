@echo off
rem my-service portable launcher (stable entry: survives app/ upgrades)
setlocal
set "HOME=%~dp0"
if "%HOME:~-1%"=="\" set "HOME=%HOME:~0,-1%"

rem Self-update: apply a staged build BEFORE starting python (nothing holds app/ yet)
if exist "%HOME%\data\update\pending.json" (
  if exist "%HOME%\data\update\app.new\src\server.py" (
    if exist "%HOME%\app.bak" rmdir /s /q "%HOME%\app.bak" >nul 2>nul
    move /y "%HOME%\app" "%HOME%\app.bak" >nul 2>nul
    move /y "%HOME%\data\update\app.new" "%HOME%\app" >nul 2>nul
    del /q "%HOME%\data\update\pending.json" >nul 2>nul
  )
)

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