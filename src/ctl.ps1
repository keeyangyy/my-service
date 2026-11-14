# my-service 宿主启停脚本（pwsh）
# 用法: (内部引擎，用户请使用 run\start.ps1 等)
#       .\ctl.ps1 [-Action] start|stop|status|restart|shortcut
param([Parameter(Position = 0)][string]$Action = "start")

$ErrorActionPreference = "Stop"
# $base = my-service 根（本脚本位于 src\ 内）
$base = Split-Path $PSScriptRoot -Parent
$py = (Get-Command python.exe).Source
New-Item -ItemType Directory -Force -Path (Join-Path $base "logs") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $base "data") | Out-Null

function Get-HostPid {
    # 首选 data\host.pid（宿主自写）；强杀可能残留，故必须校验进程存活
    $pidFile = Join-Path $base "data\host.pid"
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

function New-DashboardShortcut {
    # 生成 run\Dashboard.url：URL 取自本机 config.json 的 host/port。
    # 刻意不写 IconFile——图标交给系统按 http 协议默认处理：
    # 既不会指向别人机器上的程序路径，也不把本机安装的软件清单带进仓库。
    $shortcut = Join-Path $base "run\Dashboard.url"
    $bindHost = "127.0.0.1"
    $port = 1236
    $cfgPath = Join-Path $base "config.json"
    if (Test-Path -LiteralPath $cfgPath) {
        try {
            $cfg = Get-Content -LiteralPath $cfgPath -Raw | ConvertFrom-Json
            if ($cfg.host) { $bindHost = [string]$cfg.host }
            if ($cfg.port) { $port = [int]$cfg.port }
        } catch { }
    }
    $content = "[InternetShortcut]`r`nURL=http://${bindHost}:${port}`r`n"
    [IO.File]::WriteAllText($shortcut, $content, [Text.Encoding]::ASCII)
    Write-Host "已生成面板快捷方式: $shortcut"
}

switch ($Action.ToLower()) {
    "start" {
        if ($existing = Get-HostPid) { Write-Host "已在运行 pid=$existing"; exit 0 }
        Start-Process -FilePath $py `
            -ArgumentList @((Join-Path $base "src\server.py") -replace '\\', '/') `
            -WorkingDirectory (Join-Path $base "src") -WindowStyle Hidden
        Start-Sleep -Milliseconds 900
        if ($pid2 = Get-HostPid) { Write-Host "started pid=$pid2" } else { Write-Host "FAILED — 查看 logs\host.err.log 或 logs\host.jsonl"; exit 1 }
        # 每台机器各自生成面板快捷方式；用户自定义过就不覆盖
        if (-not (Test-Path -LiteralPath (Join-Path $base "run\Dashboard.url"))) {
            New-DashboardShortcut
        }
    }
    "stop" {
        if ($p = Get-HostPid) {
            # 只杀宿主自身（不带 /T）：被管服务生命周期独立，宿主停止/重启时不收割
            & taskkill "/PID" "$p" "/F" | Out-Null
            Remove-Item -LiteralPath (Join-Path $base "data\host.pid") -Force -ErrorAction SilentlyContinue
            Write-Host "stopped pid=$p"
        } else { Write-Host "未在运行" }
    }
    "restart" {
        & $PSCommandPath stop; Start-Sleep -Milliseconds 600; & $PSCommandPath start
    }
    "shortcut" { New-DashboardShortcut }
    default { Write-Host "用法: ctl.ps1 start|stop|status|restart|shortcut"; exit 1 }
}
