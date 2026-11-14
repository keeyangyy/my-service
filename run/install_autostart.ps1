# my-service 开机自启安装脚本（唯一方式：启动文件夹 + .lnk 快捷方式，无窗口）
#
# 用法:
#   .\run\install_autostart.ps1        # 安装
#   .\run\uninstall_autostart.ps1      # 卸载
#
# 启动链：登录 → Startup\start-my-service.lnk → pythonw.exe → server.py
# pythonw 是 GUI 子系统、没有控制台，登录时不会闪黑窗；
# .lnk 由系统外壳直接解析，不需要 wscript / cmd 这类脚本宿主。
#
# 历史：曾用 cmd → vbs（wscript SW_HIDE）→ 现为 .lnk；安装时会清理旧 vbs/cmd。
#       旧命令见 docs/analysis/pitfalls.md「自启模式演进」。
param()

$ErrorActionPreference = "Stop"
$entry = $PSScriptRoot                     # = <代码根>\run
$app = Split-Path $entry -Parent           # = app（代码根）
# $dataRoot = 数据根：MY_SERVICE_HOME 优先；便携包（上一级有 runtime\python.exe）取上一级
$dataRoot = $app
if ($env:MY_SERVICE_HOME) {
    $dataRoot = [IO.Path]::GetFullPath($env:MY_SERVICE_HOME)
} elseif (Test-Path -LiteralPath (Join-Path (Split-Path $app -Parent) "runtime\python.exe")) {
    $dataRoot = Split-Path $app -Parent
}

$startupDir = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Startup"
$lnkPath = Join-Path $startupDir "start-my-service.lnk"

# 优先 pythonw（GUI 子系统，无控制台）；便携包用 runtime 里的
$pythonw = Join-Path $dataRoot "runtime\pythonw.exe"
if (-not (Test-Path -LiteralPath $pythonw)) {
    $pythonw = Join-Path (Split-Path (Get-Command python.exe).Source) "pythonw.exe"
}
$server = Join-Path $app "src\server.py"

New-Item -ItemType Directory -Force -Path $startupDir | Out-Null
$shell = New-Object -ComObject WScript.Shell
$lnk = $shell.CreateShortcut($lnkPath)
$lnk.TargetPath       = $pythonw
$lnk.Arguments        = '"{0}"' -f $server
$lnk.WorkingDirectory = Split-Path $server -Parent
$lnk.IconLocation     = "$pythonw,0"
$lnk.Description      = "my-service 宿主（开机自启，无窗口）"
$lnk.Save()

# 清理历史 vbs/cmd 方案，避免双启动
foreach ($name in "start-my-service.vbs", "start-my-service.cmd") {
    $old = Join-Path $startupDir $name
    if (Test-Path -LiteralPath $old) {
        Remove-Item -LiteralPath $old -Force
        Write-Host "已清理历史方案: $old"
    }
}

Write-Host "✓ 已安装开机自启（无窗口，.lnk）: $lnkPath"
Write-Host "  注销重新登录即可验证；卸载: uninstall_autostart.ps1"

# 顺带生成面板快捷方式（URL 取自本机 config.json；不写 IconFile，可跨机器）
$pyCli = Join-Path $dataRoot "runtime\python.exe"
if (-not (Test-Path -LiteralPath $pyCli)) { $pyCli = (Get-Command python.exe).Source }
& $pyCli (Join-Path $app "src\service_cli.py") shortcut