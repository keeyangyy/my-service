# my-service 宿主启停脚本（pwsh）
# 用法: (内部引擎，用户请使用 run\start.ps1 等)
#       .\ctl.ps1 [-Action] start|stop|restart
param([Parameter(Position = 0)][string]$Action = "start")

$ErrorActionPreference = "Stop"
# $base = my-service 根（本脚本位于 src\ 内）
$base = Split-Path $PSScriptRoot -Parent
$py = (Get-Command python.exe).Source
New-Item -ItemType Directory -Force -Path (Join-Path $base "logs") | Out-Null

function Get-HostPid {
    # 首选 logs\host.pid（宿主自写）；强杀可能残留，故必须校验进程存活
    $pidFile = Join-Path $base "logs\host.pid"
    if (Test-Path -LiteralPath $pidFile) {
        try {
            $p = [int](Get-Content -LiteralPath $pidFile -Raw).Trim()
            if ($p -gt 0 -and (Get-Process -Id $p -ErrorAction SilentlyContinue).ProcessName -eq 'python') {
                return $p
            }
        } catch { }
    }
    # 兜底：老宿主没写 pid 文件时，以命令行精确匹配 src\server.py，避免误杀别的 python
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
        Where-Object { $_.CommandLine -match 'my-service[\\/]src[\\/]server\.py' } |
        Select-Object -First 1 -ExpandProperty ProcessId
}

switch ($Action.ToLower()) {
    "start" {
        if ($existing = Get-HostPid) { Write-Host "已在运行 pid=$existing"; exit 0 }
        Start-Process -FilePath $py `
            -ArgumentList @((Join-Path $base "src\server.py") -replace '\\', '/') `
            -WorkingDirectory (Join-Path $base "src") -WindowStyle Hidden
        Start-Sleep -Milliseconds 900
        if ($pid2 = Get-HostPid) { Write-Host "started pid=$pid2" } else { Write-Host "FAILED — 查看 logs\host.err.log 或 logs\host.jsonl"; exit 1 }
    }
    "stop" {
        if ($p = Get-HostPid) {
            & taskkill "/PID" "$p" "/T" "/F" | Out-Null
            Remove-Item -LiteralPath (Join-Path $base "logs\host.pid") -Force -ErrorAction SilentlyContinue
            Write-Host "stopped pid=$p"
        } else { Write-Host "未在运行" }
    }
    "restart" {
        & $PSCommandPath stop; Start-Sleep -Milliseconds 600; & $PSCommandPath start
    }
    default { Write-Host "用法: ctl.ps1 start|stop|status|restart"; exit 1 }
}
