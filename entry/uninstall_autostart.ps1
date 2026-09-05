# my-service 自启卸载脚本（启动文件夹 cmd + 计划任务双清理）
$ErrorActionPreference = "SilentlyContinue"
$startupCmd = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Startup\start-my-service.cmd"
if (Test-Path -LiteralPath $startupCmd) {
    Remove-Item -LiteralPath $startupCmd -Force
    Write-Host "已删除启动文件夹自启: $startupCmd"
} else {
    Write-Host "启动文件夹自启不存在（未安装）"
}
Unregister-ScheduledTask -TaskName "StartMyService" -Confirm:$false
Write-Host "已移除计划任务 StartMyService（如存在）"
