# my-service 开机自启安装脚本（位于 entry\）
# 用法:
#   .\entry\install_autostart.ps1                # 默认 Startup 模式：启动文件夹 cmd（当前方案，无需提权）
#   .\entry\install_autostart.ps1 -Mode Logon    # 计划任务 S4U 登录触发（后台房间无窗，需 gsudo 提权）
#   .\entry\install_autostart.ps1 -Mode Boot     # SYSTEM 开机任务（Session 0，需提权；注意无法脚本关停）
#   .\entry\install_autostart.ps1 -Mode Nssm     # 注册为 Windows 服务(需 nssm.exe)
param([string]$Mode = "Startup")

$ErrorActionPreference = "Stop"
$entry = $PSScriptRoot                     # = my-service\entry
$base = Split-Path $entry -Parent          # = my-service
$TaskName = "StartMyService"
$runPs = Join-Path $entry "start.ps1"

switch ($Mode.ToLower()) {
    "startup" {
        # 启动文件夹方案（当前默认）：登录时由启动体验执行，进程跑在当前用户交互会话，
        # 任意脚本/面板可直接操作。.cmd 必须 GBK + CRLF。
        $startupDir = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Startup"
        $cmdPath = Join-Path $startupDir "start-my-service.cmd"
        $content = "@echo off`r`n" +
                   "start `"`" /min pwsh -NoProfile -WindowStyle Hidden " +
                   "-ExecutionPolicy Bypass -File `"$runPs`" start`r`n"
        [IO.File]::WriteAllText($cmdPath, $content, [Text.Encoding]::GetEncoding(936))
        Write-Host "✓ 已安装启动文件夹自启: $cmdPath"
        Write-Host "  注销重新登录即可验证；卸载: uninstall_autostart.ps1"
    }
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
        $principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType S4U
        $action = New-ScheduledTaskAction -Execute "pwsh.exe" `
            -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$runPs`" start"
        $trigger = New-ScheduledTaskTrigger -AtLogOn
        $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
            -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero)
        Register-ScheduledTask -TaskName $TaskName -Principal $principal -Action $action -Trigger $trigger `
            -Settings $settings -Description "my-service 宿主登录自启（S4U 后台无窗）" | Out-Null
        Write-Host "✓ 已注册计划任务 '$TaskName' (AtLogOn: 当前用户登录即拉起 my-service)"
        Write-Host "  注销重新登录即可验证；卸载: uninstall_autostart.ps1"
    }
    "boot" {
        if (-not (Get-Command gsudo -ErrorAction SilentlyContinue)) {
            Write-Host "需要 gsudo 提权来注册 SYSTEM 计划任务"; exit 1 }
        gsudo pwsh -NoProfile -Command "
            `$action = New-ScheduledTaskAction -Execute 'pwsh.exe' ``
                -Argument '-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"`"$runPs`"`" start'
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
    default { Write-Host "未知 Mode: $Mode (可用 Startup|Logon|Boot|Nssm)"; exit 1 }
}
