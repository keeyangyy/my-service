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

## 签名（Ed25519）

- 发布时用私钥签 `latest.json` 的 `sig` 字段，签名覆盖 `version` / `url` / `sha256` / `size` / `notes_text`
- 客户端内置公钥（`src/core/update_pubkey.py`），**验签失败直接拒绝更新**；缺失签名同样拒绝
- 私钥存放在 GitHub Actions secret `UPDATE_SIGNING_KEY`；发布流水线会校验 `sig` 非空，未配置 secret 会失败
- 本地手动签名：`UPDATE_SIGNING_KEY=<hex> python tools/sign_latest.py dist/latest.json`
- 轮换密钥：`python tools/gen_update_key.py --out <私钥路径>` 生成新对 → 更新公钥文件并**同时发版**（旧客户端只认旧公钥）

> 这解决了「代理同时替换清单与包」：没有私钥就无法为新清单构造有效签名。

## 失败恢复

- 应用阶段失败：自动回滚，详情写 `data/update/last-error.json`
- 旧代码备份在 `data/bak/code-prev/`，需要时手工搬回
- 应用锁 `data/update/apply.lock` 防止更新器与 `run\start.ps1` 并发替换