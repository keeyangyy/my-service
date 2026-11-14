# my-service 公开发布前隐私检查
#
# 用法:
#   pwsh -File .\tools\check_privacy.ps1
#
# 扫描范围：`git ls-files` 列出的已跟踪文件（不含 .gitignore 排除的本地数据）。
# 命中即以非零退出，方便挂进提交前钩子或 CI。
# 说明：本脚本刻意不内置私有服务名清单——真实名字写在 tools/privacy-deny.txt
#      （已在 .gitignore 排除），一行一个词，# 开头为注释。

$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
Push-Location $root

try {
    $denyFile = Join-Path $PSScriptRoot "privacy-deny.txt"
    $deny = @()
    if (Test-Path -LiteralPath $denyFile) {
        $deny = Get-Content -LiteralPath $denyFile -Encoding utf8 |
            ForEach-Object { $_.Trim() } |
            Where-Object { $_ -and -not $_.StartsWith('#') }
    }

    $rules = @(
        @{ Name = '用户目录明文'; Pattern = '(?<![A-Za-z0-9])[A-Za-z]:[\\/]+Users[\\/]+(?<seg>[A-Za-z0-9_.-]+)' },
        @{ Name = '盘符绝对路径'; Pattern = '(?<![A-Za-z0-9])[A-Za-z]:[\\/]{1,2}(?<seg>[A-Za-z0-9_.-]{2,})' },
        @{ Name = 'API Key'; Pattern = 'sk-[A-Za-z0-9]{16,}' },
        @{ Name = 'GitHub Token'; Pattern = 'gh[pousr]_[A-Za-z0-9]{20,}' },
        @{ Name = 'Slack Token'; Pattern = 'xox[baprs]-[A-Za-z0-9-]{10,}' },
        @{ Name = 'AWS Key'; Pattern = 'AKIA[0-9A-Z]{16}' },
        @{ Name = '私钥'; Pattern = 'BEGIN (RSA |EC |OPENSSH |DSA )?PRIVATE KEY' },
        @{ Name = 'Bearer Token'; Pattern = 'Bearer\s+[A-Za-z0-9._-]{20,}' }
    )

    # 允许的占位段：示例路径统一写成 C:/path/to/...、C:/Users/<user>/...
    # 真实用户名仍会被「用户目录明文」规则单独抓住
    $allowedSegments = @('path', 'Users')

    $hits = 0
    $files = @(git ls-files)

    foreach ($f in $files) {
        if ($f -match '(^|/)\.env') {
            Write-Host ("[命中:env 文件] {0}" -f $f)
            $hits++
        }

        $full = Join-Path $root $f
        if (-not (Test-Path -LiteralPath $full)) { continue }
        $bytes = [IO.File]::ReadAllBytes($full)
        if ($bytes.Length -eq 0 -or $bytes.Length -gt 2MB) { continue }
        if ([Array]::IndexOf($bytes, [byte]0) -ge 0) { continue }   # 跳过二进制

        $lines = [Text.Encoding]::UTF8.GetString($bytes) -split "`r?`n"
        for ($i = 0; $i -lt $lines.Count; $i++) {
            $line = $lines[$i]
            foreach ($r in $rules) {
                $m = [regex]::Match($line, $r.Pattern)
                if (-not $m.Success) { continue }
                $seg = $m.Groups['seg'].Value
                if ($seg -and ($seg -in $allowedSegments)) { continue }
                Write-Host ("[命中:{0}] {1}:{2}: {3}" -f $r.Name, $f, ($i + 1), $line.Trim())
                $hits++
            }
            foreach ($d in $deny) {
                if ($line -match [regex]::Escape($d)) {
                    Write-Host ("[命中:私有名词] {0}:{1}: {2}" -f $f, ($i + 1), $line.Trim())
                    $hits++
                }
            }
        }
    }

    Write-Host ""
    if ($hits -gt 0) {
        Write-Host "✗ 发现 $hits 处可疑内容：请按 CONTRIBUTING.md「公开发布规则」替换为占位再提交。"
        exit 1
    }
    Write-Host "✓ 未发现本机路径、凭据形态或私有名词痕迹（已检查 $($files.Count) 个文件）"
    exit 0
}
finally {
    Pop-Location
}
