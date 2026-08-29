# my-service 开机自启安装脚本（位于 entry\）
# 用法:
#   .\entry\install_autostart.ps1                # 默认 Logon 模式（登录触发）
#   .\entry\install_autostart.ps1 -Mode Boot     # 系统启动触发(SYSTEM, 需提权)
#   .\entry\install_autostart.ps1 -Mode Nssm     # 注册为 Windows 服务(需 nssm.exe)
param([string]$Mode = "Logon")

$ErrorActionPreference = "Stop"
$entry = $PSScriptRoot                     # = my-service\entry
$base = Split-Path $entry -Parent          # = my-service
$TaskName = "StartMyService"
$runPs = Join-Path $entry "start.ps1"

switch ($Mode.ToLower()) {
    "logon" {
        # 计划任务根目录写入需要管理员令牌；未提权时经 gsudo 自动重跑自身
        $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
        $isAdmin = ([Security.Principal.WindowsPrincipal]$identity).IsInRole(
            [Security.Principal.WindowsBuiltInRole]::Administrator)
        if (-not $isAdmin) {
            if (-not (Get-Command gsudo -ErrorAction SilentlyContinue)) {
                Write-Host "需要管理员权限：请安装 gsudo 或以管理员身份运行本脚本"; exit 1 }
            & gsudo pwsh -NoProfile -ExecutionPolicy Bypass -File $PSCommandPath -Mode Logon
            exit $LASTEXITCODE
        }
        $action = New-ScheduledTaskAction -Execute "pwsh.exe" `
            -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$runPs`" start"
        $trigger = New-ScheduledTaskTrigger -AtLogOn
        $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
            -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero)
        Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
            -Settings $settings -Description "my-service 宿主登录自启" | Out-Null
        Write-Host "✓ 已注册计划任务 '$TaskName' (AtLogOn: 当前用户登录即拉起 my-service)"
        Write-Host "  注销重新登录即可验证；卸载: uninstall_autostart.ps1"
    }
    "boot" {
        if (-not (Get-Command gsudo -ErrorAction SilentlyContinue)) {
            Write-Host "需要 gsudo 提权来注册 SYSTEM 计划任务"; exit 1 }
        gsudo pwsh -NoProfile -Command "
            `$action = New-ScheduledTaskAction -Execute 'pwsh.exe' ``
                -Argument '-NoProfile -ExecutionPolicy Bypass -File `"`"$runPs`"`" start'
            `$trigger = New-ScheduledTaskTrigger -AtStartup
            `$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -RunLevel Highest
            `$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries ``
                -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero)
            Register-ScheduledTask -TaskName '$TaskName' -Action `$action -Trigger `$trigger ``
                -Principal `$principal -Settings `$settings | Out-Null"
        Write-Host "✓ 已注册 SYSTEM 级开机任务 '$TaskName'"
    }
    "nssm" {
        Write-Host "Nssm 模式: 请先将 nssm.exe 放入 PATH 或本目录，再运行:"
        Write-Host "  nssm install MyService $((Get-Command python.exe).Source) `"$(Join-Path $base 'src\server.py')`""
        Write-Host "  nssm set MyService AppDirectory `"$base\src`""
        exit 1
    }
    default { Write-Host "未知 Mode: $Mode (可用 Logon|Boot|Nssm)"; exit 1 }
}
