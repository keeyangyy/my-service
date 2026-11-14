# 示例按需服务的停止脚本：按 pidfile 结束进程
#
# on_demand 服务必须能"被回收"，注册条里通过 stop.script 指向本脚本。
# 默认读同目录下的 demo-console.pid（由 demo_app.py --pidfile 写入）。
param([string]$PidFile = 'demo-console.pid')

$ErrorActionPreference = 'Stop'
$pidPath = Join-Path $PSScriptRoot $PidFile

if (-not (Test-Path -LiteralPath $pidPath)) {
    Write-Host "没有 pidfile: $pidPath（可能已停止）"
    exit 0
}

try {
    $procId = [int](Get-Content -LiteralPath $pidPath -Raw).Trim()
} catch {
    Write-Host "pidfile 内容无效: $pidPath"
    exit 1
}

if (Get-Process -Id $procId -ErrorAction SilentlyContinue) {
    & taskkill /PID $procId /T /F | Out-Null
    Write-Host "已停止示例服务 pid=$procId"
} else {
    Write-Host "进程已不存在 pid=$procId"
}
Remove-Item -LiteralPath $pidPath -Force -ErrorAction SilentlyContinue
exit 0
