@echo off
rem my-service portable stop: kill host by data\host.pid
setlocal
set "HOME=%~dp0"
if "%HOME:~-1%"=="\" set "HOME=%HOME:~0,-1%"
set "PIDFILE=%HOME%\data\host.pid"

if not exist "%PIDFILE%" (
  echo my-service not running ^(no host.pid^)
  exit /b 0
)
set /p PID=<"%PIDFILE%"
if "%PID%"=="" (
  echo host.pid is empty
  exit /b 1
)
taskkill /PID %PID% /F >nul 2>nul
if errorlevel 1 (
  echo failed to stop pid=%PID% ^(already gone?^)
  exit /b 1
)
del /q "%PIDFILE%" >nul 2>nul
echo stopped pid=%PID%
exit /b 0