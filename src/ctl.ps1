# my-service 宿主启停脚本（pwsh）
# 用法: (内部引擎，用户请使用 run\start.ps1 等)
#       .\ctl.ps1 [-Action] start|stop|status|restart|shortcut
param([Parameter(Position = 0)][string]$Action = "start")

$ErrorActionPreference = "Stop"

# $app  = 代码根（本脚本位于 app\src\ 内）
# $dataRoot = 数据根：MY_SERVICE_HOME 优先；便携包（上一级有 runtime\python.exe）取上一级；否则 = $app
$app = Split-Path $PSScriptRoot -Parent
$dataRoot = $app
if ($env:MY_SERVICE_HOME) {
    $dataRoot = [IO.Path]::GetFullPath($env:MY_SERVICE_HOME)
} elseif (Test-Path -LiteralPath (Join-Path (Split-Path $app -Parent) "runtime\python.exe")) {
    $dataRoot = Split-Path $app -Parent
}

# 便携包用内置解释器；源码模式回退系统 python
$py = Join-Path $dataRoot "runtime\python.exe"
if (-not (Test-Path -LiteralPath $py)) {
    $py = (Get-Command python.exe).Source
}

New-Item -ItemType Directory -Force -Path (Join-Path $dataRoot "logs") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $dataRoot "data") | Out-Null

# 面板快捷方式：源码模式放 app\run\；便携包放包根（升级替换 app\ 时不会丢）
$shortcut = if ($dataRoot -eq $app) {
    Join-Path $app "run\Dashboard.url"
} else {
    Join-Path $dataRoot "Dashboard.url"
}

function Get-HostPid {
    # 首选 data\host.pid（宿主自写）；强杀可能残留，故必须校验进程存活
    $pidFile = Join-Path $dataRoot "data\host.pid"
    if (Test-Path -LiteralPath $pidFile) {
        try {
            $p = [int](Get-Content -LiteralPath $pidFile -Raw).Trim()
            if ($p -gt 0 -and (Get-Process -Id $p -ErrorAction SilentlyContinue).ProcessName -eq 'python') {
                return $p
            }
        } catch { }
    }
    # 兜底：老宿主没写 pid 文件时，以命令行精确匹配 src\server.py
    # （便携包路径形如 ...\app\src\server.py，同样命中；pythonw 也要认）
    Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" |
        Where-Object { $_.CommandLine -match 'src[\\/]server\.py' } |
        Select-Object -First 1 -ExpandProperty ProcessId
}

function New-DashboardShortcut {
    # 生成面板快捷方式：URL 取自本机 config.json 的 host/port。
    # 刻意不写 IconFile——图标交给系统按 http 协议默认处理：
    # 既不会指向别人机器上的程序路径，也不把本机安装的软件清单带进仓库。
    $bindHost = "127.0.0.1"
    $port = 1236
    $cfgPath = Join-Path $dataRoot "config.json"
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
            -ArgumentList @((Join-Path $app "src\server.py") -replace '\\', '/') `
            -WorkingDirectory (Join-Path $app "src") -WindowStyle Hidden
        Start-Sleep -Milliseconds 900
        if ($pid2 = Get-HostPid) { Write-Host "started pid=$pid2" } else { Write-Host "FAILED — 查看 logs\host.err.log 或 logs\host.jsonl"; exit 1 }
        # 每台机器各自生成面板快捷方式；用户自定义过就不覆盖
        if (-not (Test-Path -LiteralPath $shortcut)) {
            New-DashboardShortcut
        }
    }
    "stop" {
        if ($p = Get-HostPid) {
            # 只杀宿主自身（不带 /T）：被管服务生命周期独立，宿主停止/重启时不收割
            & taskkill "/PID" "$p" "/F" | Out-Null
            Remove-Item -LiteralPath (Join-Path $dataRoot "data\host.pid") -Force -ErrorAction SilentlyContinue
            Write-Host "stopped pid=$p"
        } else { Write-Host "未在运行" }
    }
    "restart" {
        & $PSCommandPath stop; Start-Sleep -Milliseconds 600; & $PSCommandPath start
    }
    "shortcut" { New-DashboardShortcut }
    default { Write-Host "用法: ctl.ps1 start|stop|status|restart|shortcut"; exit 1 }
}