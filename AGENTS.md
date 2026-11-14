# my-service — 项目规则入口

> 独立仓库，会被公开发布（GitHub）并部署到其他机器与其他人使用。
> 全局规则与库级规则见上层项目库的 `AGENTS.md` 与 `.agent/rules`；
> 本文件只写**本项目特有**的约束，不复述全局内容。

## 高频约束

- **框架边界**：`src/` 只放框架代码，永不出现任何被管服务的业务源码。
- **注册条是唯一发现源**：服务通过 `reg/services/<名称>.json` 接入，不要在代码里硬编码服务名单。
- **日志与数据分离**：`logs/` 只追加、可随时删；`data/` 是运行态账本（pid/状态/顺序），删了行为会变。健康探测不写访问日志。
- **零第三方依赖**：Python 标准库 + 原生 JS。不要把 `requirements.txt` / `package.json` 带进主流程。
- **只绑回环**：控制面默认 `127.0.0.1`。要改 `host` 必须同时加认证手段，否则不要改。
- **公开仓库禁止出现**：本机绝对路径、用户名、真实被管服务名、token/cookie/密钥、`reg/` `data/` `logs/` `bak/` `config.json`（均已在 `.gitignore`）。
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
| 日常操作与 REST API | [docs/guide/usage.md](docs/guide/usage.md) |
| 接入新服务的检查单 | [docs/guide/registration.md](docs/guide/registration.md) |
| 历史踩坑（冷启动慢/自启黑窗/会话绑定） | [docs/analysis/pitfalls.md](docs/analysis/pitfalls.md) |
| 公开发布规则（脱敏清单） | [CONTRIBUTING.md](CONTRIBUTING.md) |
| 威胁模型与漏洞报告 | [SECURITY.md](SECURITY.md) |

## 代码约定

- 编码 UTF-8 无 BOM、换行 LF；`.bat` / `.cmd` 例外，必须 GBK + CRLF。
- Python：标准库优先；公共函数带类型注解与一句话 docstring；模块顶部说明职责边界。
- 前端：原生 JS，DOM 引用集中在 `web/app.js` 的 `els` 表，不引框架。
- 行为变更要同步 `CHANGELOG.md`（Unreleased 段）与对应 `docs/design/*.md`。
- 新行为必须有单测；测试用临时目录，不碰用户数据与真实实例。

## 运行态约定

- 宿主与服务的启停只走 `run/*.ps1`（底层 `src/ctl.ps1`），不要直接 `python src/server.py` 长期跑。
- 面板快捷方式 `run/Dashboard.url` 由 `ctl.ps1` 按本机 `config.json` 生成，不入库、不写死浏览器路径。
- 改 `src/` 后需重启宿主才生效；被管服务进程的生命周期独立于宿主，不受重启影响。

## 改动流程

1. 定位并复现（复杂问题先确认现象）。
2. 改代码 / 文档，补单测。
3. 跑 `python -m unittest discover -s tests` 与 `pwsh -File tools/check_privacy.ps1`。
4. 更新 `CHANGELOG.md`。
5. 提交：`type(scope): 中文摘要`（`feat` / `fix` / `docs` / `test` / `chore` / `refactor`）。
