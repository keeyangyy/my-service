# my-service 宿主停止入口（通用约定 run\stop.ps1）
& (Join-Path $PSScriptRoot "..\src\ctl.ps1") stop
exit $LASTEXITCODE
