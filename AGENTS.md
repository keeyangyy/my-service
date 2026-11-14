# my-service — 项目规则入口

> 独立仓库，会被公开发布（GitHub）并部署到其他机器与其他人使用。
> 全局规则与库级规则见上层项目库的 `AGENTS.md` 与 `.agent/rules`；
> 本文件只写**本项目特有**的约束，不复述全局内容。

## 高频约束

- **框架边界**：`src/` 只放框架代码，永不出现任何被管服务的业务源码。
- **注册条是唯一发现源**：服务通过 `reg/services/<名称>.json` 接入，不要在代码里硬编码服务名单。
- **运行时数据统一在 `data/` 下**：`data/logs/` 只追加、可随时删；`data/` 其余是运行态账本与备份（pid/状态/顺序/bak），删了行为会变。健康探测不写访问日志。
- **零第三方依赖**：Python 标准库 + 原生 JS。不要把 `requirements.txt` / `package.json` 带进主流程。
- **只绑回环**：控制面默认 `127.0.0.1`。要改 `host` 必须同时加认证手段，否则不要改。
- **公开仓库禁止出现**：本机绝对路径、用户名、真实被管服务名、token/cookie/密钥、`reg/` `data/` `config.json`（均已在 `.gitignore`）。
- **提交前必须**：`python -m unittest discover -s tests` 与 `pwsh -File tools/check_privacy.ps1` 双绿。

## 按需读取

| 场景 | 文件 |
|:--|:--|
| 注册条字段、四种模式、健康判定 | [docs/design/registry.md](docs/design/registry.md) |
| 按需服务：点开即启、空闲回收 | [docs/design/on-demand.md](docs/design/on-demand.md) |
| 日志边界、登记簿事件字典 | [docs/design/logging.md](docs/design/logging.md) |
| 定时任务协议与调度语义 | [docs/design/cron.md](docs/design/cron.md) |
| 面板分组与设置项 | [docs/design/panel.md](docs/design/panel.md) |
| 控制面跨源（CORS）策略 | [docs/design/cors.md](docs/design/cors.md) |
| REST API 接口清单与约定 | [docs/design/api.md](docs/design/api.md) |
| 日常操作与常用接口 | [docs/guide/usage.md](docs/guide/usage.md) |
| 接入新服务的检查单 | [docs/guide/registration.md](docs/guide/registration.md) |
| 历史踩坑（冷启动慢/自启黑窗/会话绑定） | [docs/analysis/pitfalls.md](docs/analysis/pitfalls.md) |
| 公开发布规则（脱敏清单） | [CONTRIBUTING.md](CONTRIBUTING.md) |
| 威胁模型与漏洞报告 | [SECURITY.md](SECURITY.md) |

## 公开素材（截图 / 示例）

- 截图、演示图、示例数据**只能来自 demo 环境**：`pwsh -File examples/demo/start-demo.ps1`（独立端口 1246、独立 `demo-reg`），不要拿本机真实实例截图。
- 截图前先核对页面连的是哪个实例：看顶栏端口与注册服务名，或查 `/api/services`。
- 图片放 `docs/guide/images/`（便携包只带 `docs/guide/`，图片必须跟它在同一子树），README 只放必要的一张主面板图。
- 入库前逐张看过：不得出现真实服务名、本机路径、用户名、真实软件名。

## 代码约定

- 编码 UTF-8 无 BOM、换行 LF；`.bat` / `.cmd` 例外，必须 GBK + CRLF。
- Python：标准库优先；公共函数带类型注解与一句话 docstring；模块顶部说明职责边界。
- 前端：原生 JS，DOM 引用集中在 `web/app.js` 的 `els` 表，不引框架。
- 行为变更要同步 `CHANGELOG.md`（Unreleased 段）与对应 `docs/design/*.md`。
- 新行为必须有单测；测试用临时目录，不碰用户数据与真实实例。

## 运行态约定

- 宿主与服务的启停只走 `run/*.ps1`（底层 `src/service_cli.py`），不要直接 `python src/server.py` 长期跑。
- 面板快捷方式 `run/Dashboard.url` 由 `service_cli.py` 按本机 `config.json` 生成，不入库、不写死浏览器路径。
- 改 `src/` 后需重启宿主才生效；被管服务进程的生命周期独立于宿主，不受重启影响。

## 改动流程

1. 定位并复现（复杂问题先确认现象）。
2. 改代码 / 文档，补单测。
3. 本地检查三条全过：
   - `ruff check src tests`（本地 ruff 必须是 **0.17.5**，与 CI 一致；版本不同规则集会漂，出现过 CI 红而本地绿）
   - `python -m unittest discover -s tests`
   - `pwsh -File tools/check_privacy.ps1`
4. 更新 `CHANGELOG.md`。

## 提交规范

遵循 git-workflow：Conventional Commits，**body 必填**。

- 标题：`type(scope): 中文摘要`，祈使句、不加句号；type 用 `feat` / `fix` / `refactor` / `perf` / `docs` / `test` / `chore` / `release`。
- body：小节名独占一行、以 `：` 结尾；内容用列表（细分主题一行，说明缩进一级）；三段之间空一行。
- 日常提交用 `变更` + `原因`，必要时加 `注意`；一次提交只做一件事，不混多功能。
- 提交身份必须是公开账号：`git config --show-origin user.name` 自检，不得让实名进公开历史。
- 已发布的历史不追溯重写（v0.13.0 之前的历史保持原样）。

## 发版流程

**tag 与 Release 一律由 `.github/workflows/release.yml` 生成，不手工打 tag。** 手工推 tag 会跳过检查，
出现「流程红了 tag 还在」「有 tag 没 Release」「Release 没资产」三种半成品状态。

### 人工准备（3 步）

1. `CHANGELOG.md`：把 Unreleased 折叠为 `## vX.Y.Z（日期）`，补齐本期遗漏条目。
2. 改 `src/core/host.py` 的 `VERSION`（SemVer：新功能/重构 → MINOR+1，修复 → PATCH+1）。
3. 提交并推送：`release: 发布 vX.Y.Z`；body 首行「更新内容（自 <上一版本> 以来）：」，小节依次为
   `破坏性变更` / `新增功能` / `重构 / 性能` / `修复` / `升级说明` / `注意`。

### 触发流水线（1 条命令）

```powershell
gh workflow run release.yml -f version=X.Y.Z
gh run watch    # 盯到结束
```

也可以在仓库 Actions 页面点 `release` → Run workflow 填版本号。

### 流水线做什么

| 阶段 | 内容 |
|:--|:--|
| preflight | 必须是 master；版本号形状；与 `VERSION` 一致；CHANGELOG 有该段落；本地与远端都无重名 tag |
| check | 复用 `ci.yml`（ruff / 单测 / 覆盖率 / 前端语法），与 PR 检查同一套 |
| build | `windows-latest` 打包，并校验 `latest.json` 的 `version` / `url` / `sha256` 与 zip 一致 |
| publish | 建 annotated tag → 建 **草稿** Release → 上传 zip + `latest.json` → 资产自检 → 转正为 Latest → 从 `releases/latest/download/latest.json` 下载回来核对 sha256 |

任何一步失败都会 `gh release delete --cleanup-tag` 并删远端 tag，不留半成品；修好后重新触发即可。

### 发布说明与自检

- 发布说明取自 CHANGELOG 该版本段落，所以第 1 步的质量直接决定 Release 页面内容。
- 流水线最后的自检等价于人工核对：`Latest` 指向新版本、`latest.json` 可下载且 sha256 与产物一致、
  面板「检查更新」才能真正工作（该接口读的就是 `releases/latest/download/latest.json`）。
- 仍需人工确认：`gh api repos/<owner>/<repo>` 的 `license` 已被识别、仓库网页 README 图片正常显示。

