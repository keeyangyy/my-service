# my-service 开机自启安装脚本（唯一方式：启动文件夹 + vbs，无窗口）
#
# 用法:
#   .\run\install_autostart.ps1        # 安装
#   .\run\uninstall_autostart.ps1      # 卸载
#
# 启动链：登录 → Startup\start-my-service.vbs → wscript(SW_HIDE) → pythonw.exe → server.py
# 不依赖 pwsh；pythonw 是 GUI 子系统，不会出现控制台黑窗。
#
# 历史：曾支持 Logon（S4U 计划任务）/ Boot（SYSTEM）/ Nssm（服务）三种高级模式，
#       2026-11-14 精简为只保留本方式；旧命令见 docs/analysis/pitfalls.md「自启模式演进」。
param()

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

$startupDir = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Startup"
$vbsPath = Join-Path $startupDir "start-my-service.vbs"
$legacyCmd = Join-Path $startupDir "start-my-service.cmd"

# 优先 pythonw（GUI 子系统，无控制台）；便携包用 runtime 里的
$pythonw = Join-Path $dataRoot "runtime\pythonw.exe"
if (-not (Test-Path -LiteralPath $pythonw)) {
    $pythonw = Join-Path (Split-Path (Get-Command python.exe).Source) "pythonw.exe"
}
$server = Join-Path $app "src\server.py"

$line = 'CreateObject("WScript.Shell").Run """{0}"" ""{1}""", 0, False' -f $pythonw, $server
$content = "' my-service 开机自启（无窗口）`r`n" + $line + "`r`n"
[IO.File]::WriteAllText($vbsPath, $content, [Text.Encoding]::GetEncoding(936))

# 清理历史 cmd 方案，避免双启动
if (Test-Path -LiteralPath $legacyCmd) {
    Remove-Item -LiteralPath $legacyCmd -Force
    Write-Host "已清理历史 cmd 方案: $legacyCmd"
}

Write-Host "✓ 已安装开机自启（无窗口）: $vbsPath"
Write-Host "  注销重新登录即可验证；卸载: uninstall_autostart.ps1"

# 顺带生成面板快捷方式（URL 取自本机 config.json；不写 IconFile，可跨机器）
$pyCli = Join-Path $dataRoot "runtime\python.exe"
if (-not (Test-Path -LiteralPath $pyCli)) { $pyCli = (Get-Command python.exe).Source }
& $pyCli (Join-Path $app "src\service_cli.py") shortcut