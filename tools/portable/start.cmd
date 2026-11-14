@echo off
rem my-service portable launcher (package root = code root, no app/ layer)
setlocal
set "HOME=%~dp0"
if "%HOME:~-1%"=="\" set "HOME=%HOME:~0,-1%"

rem Apply a staged update BEFORE starting python (nothing holds the code yet):
rem data\update\ code is swapped in by src\service_cli.py update-apply (with rollback).
if exist "%HOME%\data\update\pending.json" (
  "%HOME%\runtime\python.exe" "%HOME%\src\service_cli.py" update-apply
)

rem start/stop logic lives in src\service_cli.py (shared with run\*.ps1)
"%HOME%\runtime\python.exe" "%HOME%\src\service_cli.py" start
exit /b %errorlevel%