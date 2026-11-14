# my-service 便携包构建（Windows）
#
# 产物结构（包根）：
#   runtime/            内嵌 Python（下载一次，缓存在 tools/.cache）
#   app/                代码 + 文档（升级只替换这里）
#   data/ logs/ bak/    运行态（保留）
#   reg/services|tasks/ 注册条（保留）
#   config.json         数据根配置
#   start.cmd stop.cmd  稳定入口
#
# 用法:
#   pwsh -File .\tools\build-portable.ps1
#   pwsh -File .\tools\build-portable.ps1 -Version 0.14.0 -PythonVersion 3.12.10
#   pwsh -File .\tools\build-portable.ps1 -SkipZip      # 只组装目录，方便调试
param(
    [string]$Version = "",
    [string]$OutDir = "dist",
    [string]$PythonVersion = "3.12.10",
    [string]$RepoSlug = "keeyangyy/my-service",
    [switch]$SkipZip
)

$ErrorActionPreference = "Stop"
$repo = Split-Path $PSScriptRoot -Parent
$cache = Join-Path $PSScriptRoot ".cache"

function Get-Version {
    param([string]$Repo)
    $hostPy = Join-Path $Repo "src\core\host.py"
    $m = Select-String -LiteralPath $hostPy -Pattern '^VERSION\s*=\s*"([^"]+)"'
    if (-not $m) { throw "读不到 VERSION: $hostPy" }
    return $m.Matches[0].Groups[1].Value
}

function Get-Runtime {
    param([string]$PythonVersion, [string]$Cache)
    $dir = Join-Path $Cache "python-$PythonVersion-embed-amd64"
    if (Test-Path -LiteralPath (Join-Path $dir "python.exe")) { return $dir }
    New-Item -ItemType Directory -Force -Path $Cache | Out-Null
    $zip = Join-Path $Cache "python-$PythonVersion-embed-amd64.zip"
    if (-not (Test-Path -LiteralPath $zip)) {
        $url = "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-embed-amd64.zip"
        Write-Host "下载: $url"
        Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing
    }
    Write-Host "解压 runtime -> $dir"
    Expand-Archive -LiteralPath $zip -DestinationPath $dir -Force
    return $dir
}

if (-not $Version) { $Version = Get-Version -Repo $repo }
Write-Host "构建 my-service $Version（Python $PythonVersion）"

$runtimeSrc = Get-Runtime -PythonVersion $PythonVersion -Cache $cache
$stage = Join-Path $repo "$OutDir\my-service-$Version"
if (Test-Path -LiteralPath $stage) { Remove-Item -LiteralPath $stage -Recurse -Force }
New-Item -ItemType Directory -Force -Path $stage | Out-Null

# ---- runtime（升级不动） ----
Copy-Item -LiteralPath $runtimeSrc -Destination (Join-Path $stage "runtime") -Recurse -Force

# ---- app（代码 + 文档；升级替换的对象） ----
$app = Join-Path $stage "app"
New-Item -ItemType Directory -Force -Path $app | Out-Null
foreach ($d in "src", "web", "run", "examples", "docs", "tests", "tools") {
    $src = Join-Path $repo $d
    if (Test-Path -LiteralPath $src) {
        Copy-Item -LiteralPath $src -Destination (Join-Path $app $d) -Recurse -Force
    }
}
# 打包缓存与隐私清单不进包
$cacheCopy = Join-Path $app "tools\.cache"
if (Test-Path -LiteralPath $cacheCopy) { Remove-Item -LiteralPath $cacheCopy -Recurse -Force }
$denyCopy = Join-Path $app "tools\privacy-deny.txt"
if (Test-Path -LiteralPath $denyCopy) { Remove-Item -LiteralPath $denyCopy -Force }
# 本机生成的面板快捷方式不进包（每台机器各自生成）
$dashCopy = Join-Path $app "run\Dashboard.url"
if (Test-Path -LiteralPath $dashCopy) { Remove-Item -LiteralPath $dashCopy -Force }

foreach ($f in "README.md", "CHANGELOG.md", "LICENSE", "CONTRIBUTING.md",
              "SECURITY.md", "AGENTS.md", "ruff.toml", ".coveragerc", ".editorconfig") {
    $src = Join-Path $repo $f
    if (Test-Path -LiteralPath $src) {
        Copy-Item -LiteralPath $src -Destination (Join-Path $app $f) -Force
    }
}

# ---- 数据骨架（保留） ----
foreach ($d in "data", "logs", "bak", "reg\services", "reg\tasks") {
    New-Item -ItemType Directory -Force -Path (Join-Path $stage $d) | Out-Null
}

# ---- 默认 config.json（数据根） ----
$cfg = [ordered]@{
    host                 = "127.0.0.1"
    port                 = 1236
    reg_dir              = "reg"
    audit_retention_days = 7
    cors_local_only      = $true
    monitor              = [ordered]@{ fast_sec = 1.0; idle_sec = 10.0; panel_grace_sec = 15.0 }
    scheduler            = [ordered]@{ tick_sec = 10.0 }
    backup               = [ordered]@{ keep = 7 }
}
$cfgText = ($cfg | ConvertTo-Json -Depth 4).Replace("`r`n", "`n") + "`n"
[IO.File]::WriteAllText((Join-Path $stage "config.json"), $cfgText,
                        [Text.UTF8Encoding]::new($false))

# ---- 启动器与版本 ----
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "portable\start.cmd") -Destination $stage -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "portable\stop.cmd") -Destination $stage -Force
[IO.File]::WriteAllText((Join-Path $stage "VERSION.txt"), $Version + "`n",
                        [Text.UTF8Encoding]::new($false))

Write-Host "已组装: $stage"

if ($SkipZip) { return }

# ---- 打 zip + sha256 + latest.json ----
$zipOut = Join-Path $repo "$OutDir\my-service-$Version.zip"
if (Test-Path -LiteralPath $zipOut) { Remove-Item -LiteralPath $zipOut -Force }
Compress-Archive -Path (Join-Path $stage "*") -DestinationPath $zipOut -CompressionLevel Optimal

$item = Get-Item -LiteralPath $zipOut
$hash = (Get-FileHash -LiteralPath $zipOut -Algorithm SHA256).Hash.ToLower()
$sizeMb = [math]::Round($item.Length / 1MB, 2)
Write-Host ("包: {0} ({1} MB)" -f $zipOut, $sizeMb)
Write-Host "sha256: $hash"

# latest.json 供面板一键升级读取（发布时与 zip 一起上传到 Release）
$latest = [ordered]@{
    version = $Version
    url     = "https://github.com/$RepoSlug/releases/download/v$Version/my-service-$Version.zip"
    sha256  = $hash
    size    = $item.Length
    notes   = "https://github.com/$RepoSlug/releases/tag/v$Version"
}
[IO.File]::WriteAllText((Join-Path $repo "$OutDir\latest.json"),
                        (($latest | ConvertTo-Json) -replace "`r`n", "`n") + "`n",
                        [Text.UTF8Encoding]::new($false))
Write-Host "版本清单: $OutDir\latest.json"