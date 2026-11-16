# 更新：检查 / 下载 / 应用

> 状态：Accepted ｜ 实现：`src/core/update.py`、`src/core/host.py`、`web/app.js`

## 两段式

1. **检查（只读）**：`GET /api/update/check` 拉 `latest.json`，返回新版本、安装包大小、更新说明（`notes_text`）与本次实际使用的更新源。**不下载任何包。**
2. **下载并暂存（用户确认后）**：`POST /api/update/apply` 下载 zip → 校验 → 解压到 `data/update/code.new` 并写 `pending.json`。
3. **重启应用**：宿主派生更新器，等旧进程退出后替换 `src/ web/ run/ docs/` 与三个文档，再起新宿主。

为什么必须两段：Windows 下运行中的代码目录被占用，只有「旧宿主已退出、新宿主尚未起来」这一瞬可以替换。

## GitHub 代理加速

- 默认内置 4 个**前缀型**代理，按顺序尝试：
  `githubproxy.cc`、`ghproxy.imciel.com`、`gh-proxy.com`、`ghfast.top`
- 顺序：**代理优先，官方 URL 兜底**；某个源失败自动换下一个，全部失败才报错
- 配置：`config.json` → `update.mirrors`（数组；`[]` 表示只用官方）
- 临时覆盖：环境变量 `MY_SERVICE_UPDATE_MIRRORS`（逗号分隔）
- 非 http(s) 地址（如离线 `file://`）不套代理

## 校验

清单（`latest.json`）形状校验：

- `version` 必须是 `X.Y.Z`
- `url` 必须指向本仓库的 Release 资产
- `sha256` 必须是 64 位十六进制
- `size` 必须大于 0

下载后校验：

- 实际 sha256 必须与清单一致
- 实际大小必须与清单一致
- 任一不符立即丢弃下载包并报错

> 已知边界：清单与包都经同一代理获取时，代理理论上可同时替换两者。要防这类攻击需要**发布签名**（私钥在发布流程、公钥内置客户端），目前未实现。

## 失败恢复

- 应用阶段失败：自动回滚，详情写 `data/update/last-error.json`
- 旧代码备份在 `data/bak/code-prev/`，需要时手工搬回
- 应用锁 `data/update/apply.lock` 防止更新器与 `run\start.ps1` 并发替换