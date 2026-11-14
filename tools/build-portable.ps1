# my-service 便携包构建（Windows）
#
# 产物结构（包根 = 仓库结构，不再有 app/ 层）：
#   src/ web/ run/             代码与入口
#   docs/guide/                使用说明（只带用户向文档：使用指南 + 注册指南 + 截图）
#   README.md CHANGELOG.md LICENSE
#   config.json                数据根配置
#   start.cmd stop.cmd         稳定入口（不依赖 pwsh）
#   VERSION.txt
#   runtime/                   内嵌 Python（升级不动）
#   reg/services|tasks/        注册条（用户手写，升级保留）
#   data/logs  data/bak        运行时数据骨架（升级保留）
#
# 不进包：tests/ examples/ tools/ AGENTS.md CONTRIBUTING.md SECURITY.md
#         ruff.toml .coveragerc .editorconfig .gitignore .gitattributes .github/
#         docs/{architecture.md,design/,analysis/}（开发者向，留在仓库）
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

# 包内代码目录（升级 = 按这份清单替换，见 src/service_cli.py update-apply）
$CODE_DIRS = @("src", "web", "run", "docs")
$CODE_FILES = @("README.md", "CHANGELOG.md", "LICENSE")

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
# -OutDir 支持绝对路径（便于组装到临时目录做校验）
$outBase = if ([IO.Path]::IsPathRooted($OutDir)) { $OutDir } else { Join-Path $repo $OutDir }
$stage = Join-Path $outBase "my-service-$Version"
if (Test-Path -LiteralPath $stage) { Remove-Item -LiteralPath $stage -Recurse -Force }
New-Item -ItemType Directory -Force -Path $stage | Out-Null

# ---- runtime（升级不动） ----
Copy-Item -LiteralPath $runtimeSrc -Destination (Join-Path $stage "runtime") -Recurse -Force

# ---- 代码目录（升级替换的对象） ----
foreach ($d in "src", "web", "run") {
    $src = Join-Path $repo $d
    if (Test-Path -LiteralPath $src) {
        Copy-Item -LiteralPath $src -Destination (Join-Path $stage $d) -Recurse -Force
    }
}
# 文档只带用户向的 docs/guide/（截图在 docs/guide/images/，README 引用它）
$guide = Join-Path $repo "docs\guide"
if (Test-Path -LiteralPath $guide) {
    New-Item -ItemType Directory -Force -Path (Join-Path $stage "docs") | Out-Null
    Copy-Item -LiteralPath $guide -Destination (Join-Path $stage "docs\guide") -Recurse -Force
}
# 本机生成的面板快捷方式不进包（每台机器各自生成）
$dashCopy = Join-Path $stage "run\Dashboard.url"
if (Test-Path -LiteralPath $dashCopy) { Remove-Item -LiteralPath $dashCopy -Force }

foreach ($f in "README.md", "CHANGELOG.md", "LICENSE") {
    $src = Join-Path $repo $f
    if (Test-Path -LiteralPath $src) {
        Copy-Item -LiteralPath $src -Destination (Join-Path $stage $f) -Force
    }
}

# ---- 数据骨架（升级保留） ----
foreach ($d in "data\logs", "data\bak", "reg\services", "reg\tasks") {
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
$zipOut = Join-Path $outBase "my-service-$Version.zip"
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
[IO.File]::WriteAllText((Join-Path $outBase "latest.json"),
                        (($latest | ConvertTo-Json) -replace "`r`n", "`n") + "`n",
                        [Text.UTF8Encoding]::new($false))
Write-Host "版本清单: $(Join-Path $outBase latest.json)"