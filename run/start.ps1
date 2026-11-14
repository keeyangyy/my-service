# my-service 启动入口（双击可用）
# 启停逻辑统一在 src/service_cli.py（本文件只是薄壳）
# 解释器：便携包若带 runtime\python.exe 就用它，否则用系统 PATH 上的 python
$py = Join-Path $PSScriptRoot "..\runtime\python.exe"
if (-not (Test-Path -LiteralPath $py)) { $py = (Get-Command python.exe).Source }
$cli = Join-Path $PSScriptRoot "..\src\service_cli.py"

# 有暂存的升级就先应用（无暂存时是 no-op）
& $py $cli update-apply
& $py $cli start
exit $LASTEXITCODE