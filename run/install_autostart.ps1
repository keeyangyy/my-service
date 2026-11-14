# my-service 开机自启安装脚本（位于 run\）
# 用法:
#   .\run\install_autostart.ps1                # 默认 Startup 模式：启动文件夹 cmd（当前方案，无需提权）
#   .\run\install_autostart.ps1 -Mode Logon    # 计划任务 S4U 登录触发（后台房间无窗，需 gsudo 提权）
#   .\run\install_autostart.ps1 -Mode Boot     # SYSTEM 开机任务（Session 0，需提权；注意无法脚本关停）
#   .\run\install_autostart.ps1 -Mode Nssm     # 注册为 Windows 服务(需 nssm.exe)
param([string]$Mode = "Startup")

$ErrorActionPreference = "Stop"
$entry = $PSScriptRoot                     # = app\run
$app = Split-Path $entry -Parent           # = app（代码根）
# $dataRoot = 数据根：MY_SERVICE_HOME 优先；便携包（上一级有 runtime\python.exe）取上一级
$dataRoot = $app
if ($env:MY_SERVICE_HOME) {
    $dataRoot = [IO.Path]::GetFullPath($env:MY_SERVICE_HOME)
} elseif (Test-Path -LiteralPath (Join-Path (Split-Path $app -Parent) "runtime\python.exe")) {
    $dataRoot = Split-Path $app -Parent
}
$TaskName = "StartMyService"
$runPs = Join-Path $entry "start.ps1"
$portableLauncher = Join-Path $dataRoot "start.cmd"

switch ($Mode.ToLower()) {
    "startup" {
        # 启动文件夹方案（默认）：登录时由启动体验执行，进程跑在当前用户交互会话。
        # 写 .vbs 而不是 .cmd：wscript 以 SW_HIDE 启动 pythonw（GUI 子系统），
        # 两者都不创建控制台窗口 → 不再出现历史那种 conhost 黑窗。
        # 便携包用 runtime\pythonw.exe；源码模式用系统同名 pythonw。
        $startupDir = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Startup"
        $vbsPath = Join-Path $startupDir "start-my-service.vbs"
        $legacyCmd = Join-Path $startupDir "start-my-service.cmd"
        $pythonw = Join-Path $dataRoot "runtime\pythonw.exe"
        if (-not (Test-Path -LiteralPath $pythonw)) {
            $pythonw = Join-Path (Split-Path (Get-Command python.exe).Source) "pythonw.exe"
        }
        $server = Join-Path $app "src\server.py"
        $line = 'CreateObject("WScript.Shell").Run """{0}"" ""{1}""", 0, False' -f $pythonw, $server
        $content = "' my-service 开机自启（无窗口）`r`n" + $line + "`r`n"
        [IO.File]::WriteAllText($vbsPath, $content, [Text.Encoding]::GetEncoding(936))
        if (Test-Path -LiteralPath $legacyCmd) {
            Remove-Item -LiteralPath $legacyCmd -Force
            Write-Host "已清理历史 cmd 方案（避免双启动）: $legacyCmd"
        }
        Write-Host "✓ 已安装启动文件夹自启（无窗口）: $vbsPath"
        Write-Host "  注销重新登录即可验证；卸载: uninstall_autostart.ps1"
        # 顺带生成面板快捷方式（URL 取自本机 config.json；不写 IconFile，可跨机器）
        & (Join-Path $app "src\ctl.ps1") shortcut
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
        $pyHint = Join-Path $dataRoot "runtime\python.exe"
        if (-not (Test-Path -LiteralPath $pyHint)) { $pyHint = (Get-Command python.exe).Source }
        Write-Host "Nssm 模式: 请先将 nssm.exe 放入 PATH 或本目录，再运行:"
        Write-Host "  nssm install MyService `"$pyHint`" `"$(Join-Path $app 'src\server.py')`""
        Write-Host "  nssm set MyService AppDirectory `"$app\src`""
        Write-Host "  （数据根: $dataRoot）"
        exit 1
    }
    default { Write-Host "未知 Mode: $Mode (可用 Startup|Logon|Boot|Nssm)"; exit 1 }
}
