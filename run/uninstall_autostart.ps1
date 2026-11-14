# my-service 自启卸载（启动文件夹 vbs / 历史 cmd / 计划任务一并清理）
#
# 用法: .\run\uninstall_autostart.ps1
$ErrorActionPreference = "SilentlyContinue"

$startupDir = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Startup"
$found = $false
foreach ($name in "start-my-service.vbs", "start-my-service.cmd") {
    $path = Join-Path $startupDir $name
    if (Test-Path -LiteralPath $path) {
        Remove-Item -LiteralPath $path -Force
        Write-Host "已删除: $path"
        $found = $true
    }
}
if (-not $found) { Write-Host "启动文件夹自启不存在（未安装）" }

Unregister-ScheduledTask -TaskName "StartMyService" -Confirm:$false
Write-Host "已移除计划任务 StartMyService（如存在）"