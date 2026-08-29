# my-service 宿主启动入口（通用约定 entry\start.ps1）
# 实现细节在根部 run.ps1；此壳保持 services 目录协议一致。
& (Join-Path $PSScriptRoot "..\src\ctl.ps1") start
exit $LASTEXITCODE
