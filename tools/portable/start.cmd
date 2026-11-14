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

rem start/stop Âß¼­Í³Ò»ÔÚ app\src\service_cli.py
"%HOME%\runtime\python.exe" "%HOME%\app\src\service_cli.py" start
exit /b %errorlevel%