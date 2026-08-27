# my-service 自启卸载脚本
$ErrorActionPreference = "SilentlyContinue"
Unregister-ScheduledTask -TaskName "StartMyService" -Confirm:$false
Write-Host "已移除计划任务 StartMyService（如存在）"
