# my-service 宿主启停脚本（pwsh）
# 用法: .\run.ps1 [-Action] start|stop|status|restart
param([Parameter(Position = 0)][string]$Action = "start")

$ErrorActionPreference = "Stop"
$here = $PSScriptRoot
$py = (Get-Command python.exe).Source
$logOut = Join-Path $here "logs\host.out.log"
New-Item -ItemType Directory -Force -Path (Join-Path $here "logs") | Out-Null

function Get-HostPid {
    # 以命令行精确匹配，避免误杀别的 python 进程
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
        Where-Object { $_.CommandLine -match 'my-service[\\/]server\.py' } |
        Select-Object -First 1 -ExpandProperty ProcessId
}

switch ($Action.ToLower()) {
    "start" {
        if ($existing = Get-HostPid) { Write-Host "已在运行 pid=$existing"; exit 0 }
        Start-Process -FilePath $py `
            -ArgumentList @((Join-Path $here "server.py") -replace '\\', '/') `
            -WorkingDirectory $here -WindowStyle Hidden
        Start-Sleep -Milliseconds 900
        if ($pid2 = Get-HostPid) { Write-Host "started pid=$pid2" } else { Write-Host "FAILED — 查看 logs\host.err.log"; exit 1 }
    }
    "stop" {
        if ($p = Get-HostPid) {
            & taskkill "/PID" "$p" "/T" "/F" | Out-Null
            Write-Host "stopped pid=$p"
        } else { Write-Host "未在运行" }
    }
    "status" {
        if ($p = Get-HostPid) { Write-Host "running pid=$p" } else { Write-Host "stopped" }
    }
    "restart" {
        & $PSCommandPath stop; Start-Sleep -Milliseconds 600; & $PSCommandPath start
    }
    default { Write-Host "用法: run.ps1 start|stop|status|restart"; exit 1 }
}
