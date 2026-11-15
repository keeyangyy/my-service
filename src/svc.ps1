# my-service 服务管理 CLI（薄壳）
#
# 全部逻辑在 src\service_cli.py（唯一实现）；本文件只做命令名映射与参数透传，
# 供终端 / AI 会话 / 脚本用。服务管理命令走宿主 REST API，宿主未运行时直接报错。
#
# 用法:
#   .\src\svc.ps1 ls                        列出全部服务与状态
#   .\src\svc.ps1 <start|stop|restart> <name>
#   .\src\svc.ps1 crons                     列出定时任务
#   .\src\svc.ps1 cron-run <name>           手动立即触发定时任务
#   .\src\svc.ps1 events [name] [n=50]      尾读启停登记簿（可按服务/任务名过滤）
#   .\src\svc.ps1 host-err [n]              尾读宿主异常栈
# 注：服务自身的业务日志由服务自己管（<服务目录>\logs\），框架不收集、故本 CLI 不提供读取
param(
    [Parameter(Position = 0)][string]$Cmd = "ls",
    [Parameter(Position = 1)][string]$Name,
    [Parameter(Position = 2)][int]$N = 50
)

$ErrorActionPreference = "Stop"

$app = Split-Path $PSScriptRoot -Parent

$map = @{
    "ls"       = "ls"
    "start"    = "svc-start"
    "stop"     = "svc-stop"
    "restart"  = "svc-restart"
    "crons"    = "crons"
    "cron-run" = "cron-run"
    "events"   = "events"
    "host-err" = "host-err"
}
$action = $map[$Cmd.ToLower()]
if (-not $action) {
    Write-Host "用法: svc.ps1 ls | <start|stop|restart> <name> | crons | cron-run <name> | events [name] [n] | host-err [n]"
    exit 1
}

# 解释器：包内 runtime 优先（-WithRuntime 时），否则系统 python
$py = Join-Path $app "runtime\python.exe"
if (-not (Test-Path -LiteralPath $py)) { $py = (Get-Command python.exe).Source }
$cli = Join-Path $app "src\service_cli.py"

$cliArgs = @($cli, $action)
if ($Name) { $cliArgs += $Name }
if ($N -ne 50) { $cliArgs += $N }

& $py @cliArgs
exit $LASTEXITCODE
