# my-service 宿主重启入口（通用约定 run\restart.ps1）
& (Join-Path $PSScriptRoot "..\src\ctl.ps1") restart
exit $LASTEXITCODE
