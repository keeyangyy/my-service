# 贡献指南

## 环境

- Windows 10/11
- Python 3.10+（开发与测试环境为 3.14）
- PowerShell 7（`pwsh`）

项目**只依赖 Python 标准库 + 原生 JS**，不引入第三方运行时依赖。提 PR 时请不要把
`requirements.txt`、`package.json` 之类带进主流程——这是刻意的取舍，不是遗漏。

## 本地开发

```powershell
git clone <repo-url>
cd <repo-dir>
pwsh -File .\run\start.ps1        # 启动宿主（127.0.0.1:1236）
python -m unittest discover -s tests   # 全量单测
```

- 面板：<http://127.0.0.1:1236>
- 日常排查：`data/logs/host.jsonl`（登记簿）、`data/logs/host.err.log`（异常栈）
- 首次 `start` 会按本机 `config.json` 生成 `run/Dashboard.url`（不入库）

## 代码约定

- 编码 UTF-8（无 BOM）、换行 LF；`.bat` / `.cmd` 例外，必须 GBK + CRLF
- Python：标准库优先；公共函数写类型注解与一句话 docstring；模块顶部说明职责边界
- 前端：原生 JS，不引框架；DOM 操作集中在 `web/app.js` 的 `els` 表
- 行为变更要同时更新 `CHANGELOG.md` 与对应 `docs/design/*.md`
- 单测覆盖新增行为；测试用临时目录，不碰用户数据

## 提交规范

提交信息用 `type(scope): 摘要`，摘要用中文，与 `CHANGELOG.md` 的措辞保持一致：

```
feat(backup): 备份保留份数可配置，默认 7
fix(runner): 端口探测改用 localhost 兼容 IPv6 监听
docs(readme): 补齐快速开始与安全边界
```

## 公开发布规则（脱敏清单）

本仓库是公开仓库，**提交前必须确认下面每一项**：

| 禁止提交 | 用这些占位替代 |
|:--|:--|
| 本机绝对路径（`C:/Users/<你的用户名>/...`、任意盘符的私人目录） | `C:/path/to/your-service`、`<repo>` |
| 真实用户名、主机名、内网 IP | `<user>`、`<host>`、`127.0.0.1` |
| 你实际接入的第三方服务/软件名（尤其是小众商业软件） | `your-service`、`some-service`、`<vendor>/<app>` |
| token / cookie / 密钥 / 账号密码 / `.env` | 环境变量名或占位符，永远不要写值 |
| `reg/`、`data/`（含 logs/、bak/）、`config.json` | 已由 `.gitignore` 排除，不要 `git add -f` |
| 本机浏览器/软件安装路径（如快捷方式的 `IconFile`） | 交给系统默认处理，或按本机生成 |

新增示例、文档、测试时请直接用泛化名——**不要**先写真实名再回头改，历史里会留下痕迹。

提交前跑一遍检查脚本：

```powershell
pwsh -File .\tools\check_privacy.ps1
```

脚本扫描已跟踪文件里的用户目录明文、盘符绝对路径、常见凭据形态与私有服务名清单，
有命中就非零退出。CI 建立后会作为必过项。

## PR 流程

1. 从默认分支切分支：`fix/...`、`feat/...`
2. 改动 + 单测 + `check_privacy.ps1` 全绿
3. PR 描述写清：改了什么、为什么、怎么验证的
4. 涉及接口/配置字段变更时，同步更新 `docs/` 与 `examples/`
