# my-service 服务管理 CLI 薄壳（位于 entry\，带参数供终端/AI 会话使用；内部全部转调 REST API）
# 用法:
#   .\entry\svc.ps1 ls                      列出全部服务与状态
#   .\entry\svc.ps1 <start|stop|restart> <name>
#   .\entry\svc.ps1 log <name> [n=50]      尾读日志
param(
    [Parameter(Position = 0)][string]$Cmd = "ls",
    [Parameter(Position = 1)][string]$Name,
    [Parameter(Position = 2)][int]$N = 50
)

$ErrorActionPreference = "Stop"
$Base = "http://127.0.0.1:1236"

function Invoke-Api([string]$Method, [string]$Path, $Body = $null) {
    $args_ = @{ Method = $Method; Uri = "$Base$Path"; TimeoutSec = 8 }
    if ($null -ne $Body) { $args_.Body = ($Body | ConvertTo-Json); $args_.ContentType = "application/json" }
    try { Invoke-RestMethod @args_ }
    catch { Write-Host "[连接失败] 先运行 .\entry\start.ps1 启动宿主 ($($_.Exception.Message))"; exit 1 }
}

switch ($Cmd.ToLower()) {
    "ls" {
        $r = Invoke-Api GET "/api/services"
        $r.services | Format-Table @{n = "状态"; e = { $_.status } },
                                   @{n = "名称"; e = { $_.display } },
                                   @{n = "ID"; e = { $_.name } },
                                   @{n = "端口"; e = { $_.port } },
                                   @{n = "uptime"; e = { $_.uptime_sec } },
                                   @{n = "自启"; e = { $_.autostart } },
                                   @{n = "备注"; e = { $_.error } } -AutoSize
    }
    "start" { $r = Invoke-Api POST "/api/services/$Name/start";  Write-Host "$Name -> $($r.status) ($($r.message))" }
    "stop"  { $r = Invoke-Api POST "/api/services/$Name/stop";   Write-Host "$Name -> $($r.status) ($($r.message))" }
    "restart" { $r = Invoke-Api POST "/api/services/$Name/restart"; Write-Host "$Name -> $($r.status) ($($r.message))" }
    "log" {
        $r = Invoke-Api GET "/api/logs/$Name`?n=$N"
        $r.lines | ForEach-Object { $_ }
    }
    default { Write-Host "用法: svc.ps1 ls | log <name> [n] | start|stop|restart <name>"; exit 1 }
}
