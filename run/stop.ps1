# my-service 停止入口（双击可用）
# 启停逻辑统一在 src/service_cli.py（本文件只是薄壳）
$py = Join-Path $PSScriptRoot "..\runtime\python.exe"
if (-not (Test-Path -LiteralPath $py)) { $py = (Get-Command python.exe).Source }
& $py (Join-Path $PSScriptRoot "..\src\service_cli.py") stop
exit $LASTEXITCODE