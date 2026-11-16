# my-service 便携包构建（Windows）
#
# 产物结构（zip 顶层是 my-service-<版本>/，包内 = 仓库结构；只做「裁切」：不打测试/工具/开发者文档，也不打 Python）：
#   src/ web/ run/             代码与入口（双击 run\start.ps1 启停，需要系统 Python）
#   docs/guide/                使用说明（用户向：使用指南 + 注册指南 + 截图）
#   README.md CHANGELOG.md LICENSE
#   reg/services|tasks/        注册条（用户手写，升级保留）
#   data/logs  data/bak        运行时数据骨架（升级保留）
#
# 依赖：目标机器需要 Python 3.10+（开发与测试环境为 3.14）。
#       要打包给没装 Python 的机器，加 -WithRuntime（包根多一个 runtime/，约 +11 MB）。
#
# 不进包：tests/ examples/ tools/ AGENTS.md CONTRIBUTING.md SECURITY.md
#         ruff.toml .coveragerc .editorconfig .gitignore .gitattributes .github/
#         docs/{architecture.md,design/,analysis/}（开发者向，留在仓库）
#
# 用法:
#   pwsh -File .\tools\build-portable.ps1
#   pwsh -File .\tools\build-portable.ps1 -Version 0.14.0
#   pwsh -File .\tools\build-portable.ps1 -WithRuntime     # 连 Python 一起打
#   pwsh -File .\tools\build-portable.ps1 -SkipZip         # 只组装目录，方便调试
param(
    [string]$Version = "",
    [string]$OutDir = "dist",
    [string]$PythonVersion = "3.12.10",
    [string]$RepoSlug = "keeyangyy/my-service",
    [switch]$WithRuntime,
    [switch]$SkipZip
)

$ErrorActionPreference = "Stop"
$repo = Split-Path $PSScriptRoot -Parent
$cache = Join-Path $PSScriptRoot ".cache"

# 包内代码目录（升级 = 按这份清单替换，见 src/core/update.py 的 CODE_DIRS / CODE_FILES）
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
Write-Host "构建 my-service $Version"

# -OutDir 支持绝对路径（便于组装到临时目录做校验）
$outBase = if ([IO.Path]::IsPathRooted($OutDir)) { $OutDir } else { Join-Path $repo $OutDir }
$stage = Join-Path $outBase "my-service-$Version"
if (Test-Path -LiteralPath $stage) { Remove-Item -LiteralPath $stage -Recurse -Force }
New-Item -ItemType Directory -Force -Path $stage | Out-Null

# ---- 可选：内嵌 Python（默认不打，目标机器用系统 Python） ----
if ($WithRuntime) {
    Write-Host "打包内嵌 Python $PythonVersion（-WithRuntime）"
    $runtimeSrc = Get-Runtime -PythonVersion $PythonVersion -Cache $cache
    Copy-Item -LiteralPath $runtimeSrc -Destination (Join-Path $stage "runtime") -Recurse -Force
}

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

# ---- 不生成 config.json ----
# 配置是「用户数据」不是「发行物」：缺省时走 server.py 的 DEFAULT_CONFIG，
# 面板「设置」保存时自动创建/写回。这样包内不含任何可被误当模板的配置副本。


Write-Host "已组装: $stage"

if ($SkipZip) { return }

# ---- 打 zip + sha256 + latest.json ----
$zipOut = Join-Path $outBase "my-service-$Version.zip"
if (Test-Path -LiteralPath $zipOut) { Remove-Item -LiteralPath $zipOut -Force }
Compress-Archive -Path $stage -DestinationPath $zipOut -CompressionLevel Optimal

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
Write-Host "版本清单: $(Join-Path $outBase 'latest.json')"