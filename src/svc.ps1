# my-service 服务管理 CLI（位于 src\，程序化接口层：终端/AI 会话/脚本用，非双击入口；内部全部转调 REST API）
# 用法:
#   .\src\svc.ps1 ls                        列出全部服务与状态
#   .\src\svc.ps1 <start|stop|restart> <name>
#   .\src\svc.ps1 events [name] [n=50]      尾读启停登记簿（可按服务名过滤）
#   .\src\svc.ps1 host-err [n]              尾读宿主异常栈
# 注：服务自身的业务日志由服务自己管（<服务目录>\logs\），框架不收集、故本 CLI 不提供读取
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
    catch { Write-Host "[连接失败] 先运行 C:\path\to\my-service\entry\start.ps1 启动宿主 ($($_.Exception.Message))"; exit 1 }
}

function Show-Events([string]$Svc, [int]$Count) {
    $q = "/api/audit?n=$Count"
    if ($Svc) { $q += "&svc=$([Uri]::EscapeDataString($Svc))" }
    (Invoke-Api GET $q).events | Format-Table @{n = "时间"; e = { $_.ts } },
                                               @{n = "服务"; e = { if ($_.svc) { $_.svc } else { "(host)" } } },
                                               @{n = "动作"; e = { $_.act } },
                                               @{n = "成败"; e = { if ($_.ok) { 'ok' } else { 'FAIL' } } },
                                               @{n = "耗时ms"; e = { $_.dur_ms } },
                                               @{n = "pid"; e = { $_.pid } },
                                               @{n = "备注"; e = { $_.note } } -AutoSize
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
    "start" { $r = Invoke-Api POST "/api/services/$Name/start?via=cli";  Write-Host "$Name -> $($r.status) ($($r.message))" }
    "stop"  { $r = Invoke-Api POST "/api/services/$Name/stop";   Write-Host "$Name -> $($r.status) ($($r.message))" }
    "restart" { $r = Invoke-Api POST "/api/services/$Name/restart"; Write-Host "$Name -> $($r.status) ($($r.message))" }
    "events" { Show-Events -Svc $(if ($Name -eq 'all') { '' } else { $Name }) -Count $N }
    "host-err" {
        if ($Name) { $N = $Name }
        (Invoke-Api GET "/api/host-err?n=$N").lines | ForEach-Object { $_ }
    }
    default {
        Write-Host "用法: svc.ps1 ls | events [name] [n] | host-err [n] | start|stop|restart <name>"
        exit 1
    }
}

