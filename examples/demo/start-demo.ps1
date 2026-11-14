# my-service 示例环境：把 demo 复制到临时目录，用独立端口起一个演示实例
#
# 用法:
#   pwsh -File .\examples\demo\start-demo.ps1              # 起在 http://127.0.0.1:1246
#   pwsh -File .\examples\demo\start-demo.ps1 -Port 1256   # 换端口
#   pwsh -File .\examples\demo\start-demo.ps1 -NoBrowser   # 不自动开浏览器
#   pwsh -File .\examples\demo\start-demo.ps1 -Stop        # 停掉 demo 宿主与示例服务
param(
    [int]$Port = 1246,
    [string]$WorkDir = (Join-Path $env:TEMP 'my-service-demo'),
    [switch]$NoBrowser,
    [switch]$Stop
)

$ErrorActionPreference = 'Stop'
$demoDir = $PSScriptRoot                                    # examples\demo
$repoDir = Split-Path (Split-Path $demoDir -Parent) -Parent  # 项目根
$demoPorts = 33510, 33511, 33512

function Stop-Demo {
    $pidFile = Join-Path $WorkDir 'data\host.pid'
    if (Test-Path -LiteralPath $pidFile) {
        try {
            $hostPid = [int](Get-Content -LiteralPath $pidFile -Raw).Trim()
            if (Get-Process -Id $hostPid -ErrorAction SilentlyContinue) {
                & taskkill /PID $hostPid /F | Out-Null
                Write-Host "已停止 demo 宿主 pid=$hostPid"
            }
        } catch { }
    }
    foreach ($p in $demoPorts) {
        $conns = Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue
        foreach ($owner in ($conns.OwningProcess | Sort-Object -Unique)) {
            & taskkill /PID $owner /F | Out-Null
            Write-Host "已停止示例服务 pid=$owner（端口 $p）"
        }
    }
}

if ($Stop) { Stop-Demo; exit 0 }

# 先清掉上一次的 demo（幂等）
Stop-Demo
if (Test-Path -LiteralPath $WorkDir) {
    Remove-Item -LiteralPath $WorkDir -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $WorkDir | Out-Null

# 复制运行时三件套与示例服务
foreach ($d in 'src', 'web', 'run') {
    Copy-Item -LiteralPath (Join-Path $repoDir $d) -Destination $WorkDir -Recurse -Force
}
Copy-Item -LiteralPath (Join-Path $demoDir 'services') -Destination $WorkDir -Recurse -Force

# demo 自己的 config.json（独立端口 + 独立注册目录）
$cfg = [ordered]@{
    host                 = '127.0.0.1'
    port                 = $Port
    reg_dir              = 'demo-reg'
    audit_retention_days = 7
    cors_local_only      = $true
    monitor              = [ordered]@{ fast_sec = 1.0; idle_sec = 10.0; panel_grace_sec = 15.0 }
    scheduler            = [ordered]@{ tick_sec = 10.0 }
    backup               = [ordered]@{ keep = 7 }
}
$cfgText = ($cfg | ConvertTo-Json -Depth 4).Replace("`r`n", "`n") + "`n"
[IO.File]::WriteAllText((Join-Path $WorkDir 'config.json'), $cfgText, [Text.UTF8Encoding]::new($false))

# 注册条：{{WORKDIR}} → 实际路径（统一正斜杠）
$slash = $WorkDir -replace '\\', '/'
foreach ($kind in 'services', 'tasks') {
    $src = Join-Path $demoDir "reg\$kind"
    $dst = Join-Path $WorkDir "demo-reg\$kind"
    New-Item -ItemType Directory -Force -Path $dst | Out-Null
    Get-ChildItem -LiteralPath $src -Filter *.json | ForEach-Object {
        $text = [IO.File]::ReadAllText($_.FullName).Replace('{{WORKDIR}}', $slash)
        [IO.File]::WriteAllText((Join-Path $dst $_.Name), $text, [Text.UTF8Encoding]::new($false))
    }
}

# 起宿主
$py = (Get-Command python.exe).Source
Start-Process -FilePath $py `
    -ArgumentList @((Join-Path $WorkDir 'src\server.py') -replace '\\', '/') `
    -WorkingDirectory (Join-Path $WorkDir 'src') -WindowStyle Hidden
Start-Sleep -Milliseconds 1500

$url = "http://127.0.0.1:$Port/"
Write-Host "demo 已启动: $url"
Write-Host "工作目录:    $WorkDir"
Write-Host "停止:        pwsh -File `"$PSCommandPath`" -Stop"
if (-not $NoBrowser) { Start-Process $url }
