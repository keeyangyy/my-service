# my-service 重启入口（双击可用）
# 启停逻辑统一在 src/service_cli.py（本文件只是薄壳）
& (Get-Command python.exe).Source (Join-Path $PSScriptRoot "..\src\service_cli.py") restart
exit $LASTEXITCODE