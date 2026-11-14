@echo off
rem my-service portable stop: delegate to src\service_cli.py
setlocal
set "HOME=%~dp0"
if "%HOME:~-1%"=="\" set "HOME=%HOME:~0,-1%"
"%HOME%\runtime\python.exe" "%HOME%\src\service_cli.py" stop
exit /b %errorlevel%