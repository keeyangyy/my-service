# my-service

本机服务管理框架：把散落在不同目录里的本地服务（网页面板、常驻进程、定时脚本）收进一个面板，
统一启停、自启与跳转。

只依赖 **Python 标准库 + 原生 JS**，没有第三方运行时依赖；控制面默认只监听 `127.0.0.1`。

## 它解决什么问题

- **服务散在各处**：每个服务一个目录、一套启停命令 → 在 `reg/` 放一条 JSON 就接入，面板与 CLI 统一操作。
- **忘记关、端口被占**：内置健康探测与状态机，能收敛「启动中」这类卡死状态，按需服务空闲自动回收。
- **开机要手动一个个点**：一条命令安装自启（启动文件夹 / 登录计划任务 / 系统服务）。
- **谁在跑、什么时候起的**：控制面写结构化登记簿 `logs/host.jsonl`，面板「事件」直接看。

## 特性

| 能力 | 说明 |
|:--|:--|
| 注册即接入 | `reg/services/<名称>.json` 一个文件就是一个服务，改完刷新面板即生效，不用重启宿主 |
| 四种形态 | `managed` 常驻托管、`detached` 只管外部进程、`on_demand` 点开即启+空闲回收、`task` 定时跑一次 |
| 健康判定 | `http` 探测地址或 `port` 端口探测；失败按状态机收敛为 `failed`，不会永久卡在 `starting` |
| 定时任务 | `reg/tasks/<名称>.json` 声明 `daily` / `interval` 等调度，宿主内调度器到点执行 |
| 面板 | 分组卡片、拖拽排序、服务/任务详情、日志查看、设置弹窗、宿主自身重启与停止 |
| 备份与保留 | 一键备份 `reg/` + `config.json` + `adapters/` 到 `bak/`，只留最近 N 份（默认 7） |
| 跨源边界 | 默认只给本机来源的网页回 CORS 头，跨源写操作一律拒绝 |
| 零依赖 | Python 标准库 + 原生 JS，克隆即可跑 |

## 环境要求

- Windows 10 / 11
- Python 3.10+（开发与测试环境为 3.14）
- PowerShell 7（`pwsh`）

## 快速开始

```powershell
git clone <repo-url>
cd C:\path\to\my-service
pwsh -File .\run\start.ps1          # 启动宿主（后台隐藏窗口）
start http://127.0.0.1:1236         # 打开面板
```

首次启动会按本机 `config.json` 生成 `run\Dashboard.url`（双击即可开面板；该文件不入库）。

想先看效果，可以把 `examples/` 里的示例注册条复制过去：

```powershell
Copy-Item .\examples\reg\services\example-web.json .\reg\services\
Copy-Item .\examples\reg\tasks\example-task.json .\reg\tasks\
```

常驻 / 停止：

```powershell
pwsh -File .\run\stop.ps1
pwsh -File .\run\restart.ps1
.\run\install_autostart.ps1         # 安装开机自启（默认启动文件夹方案）
```

## 注册第一个服务（三步）

1. 服务目录里补齐三件套：**启动入口**、**停止方式**、**健康判定**。
2. 在 `reg\services\` 放一条注册条。
3. 刷新面板 —— 服务出现，无需重启宿主。

最小注册条（完整字段见 [docs/design/registry.md](docs/design/registry.md)）：

```json
{
  "path": "C:/path/to/your-service",
  "display": "your-service",
  "description": "示例：带网页面板的本机服务",
  "mode": "on_demand",
  "url": "http://127.0.0.1:33510/",
  "start": { "entry": "src/server.py" },
  "stop": { "script": "run/stop.ps1" },
  "health": { "type": "http", "url": "http://127.0.0.1:33510/health", "timeout_sec": 30 },
  "identity": { "pidfile": "data/server.pid" },
  "runtime": { "enabled": true, "autostart": false, "idle_timeout_sec": 600 }
}
```

### 四种形态

| `mode` | 行为 | 典型场景 |
|:--|:--|:--|
| `managed` | 宿主负责启动、停止、健康判定、退出码回收 | 自己写的常驻服务、脚本 |
| `detached` | 只做健康探测与状态展示，不接管生命周期 | 官方安装包自带的进程（有自己的 pidfile/启动脚本） |
| `on_demand` | 点开面板时才启动，空闲超时自动停 | 偶尔看的仪表盘 |
| `task` | 无进程，按 `schedule` 到点跑一次脚本 | 签到、快照、清理 |

## 定时任务

放在 `reg\tasks\<名称>.json`，调度类型与参数见 [docs/design/cron.md](docs/design/cron.md)；
面板里可直接看上次结果、手动「立即运行」。

## 目录结构

```
my-service/
├── README.md
├── config.json                # host / port / reg_dir / 保留天数 / CORS 开关 / 备份保留份数
├── reg/
│   ├── services/<名称>.json   # 服务注册条（唯一发现源）
│   └── tasks/<名称>.json      # 定时任务注册条
├── adapters/                  # 可选转接层：<名称>/ 下放薄适配脚本，自动优先命中
├── run/                       # 人的入口
│   ├── start.ps1 / stop.ps1 / restart.ps1
│   ├── install_autostart.ps1 / uninstall_autostart.ps1
│   └── Dashboard.url          # 首次启动时按本机配置生成（不入库）
├── logs/                      # 记录：host.jsonl 登记簿、host.err.log（不入库）
├── data/                      # 数据：host.pid、pids/、cron_state.json（不入库）
├── bak/                       # 实例配置备份，保留最近 N 份（不入库）
├── src/
│   ├── server.py              # 组装入口（config → Scanner → Manager → Scheduler → HTTP）
│   ├── ctl.ps1                # 启停内部引擎（run/ 的底层）
│   ├── svc.ps1                # 终端 / AI 会话用的服务管理 CLI
│   └── core/                  # api / state / scheduler / runner / scanner / audit / backup
├── web/                       # 前端静态资源（index.html / app.js / style.css / opening.html）
├── tools/
│   └── check_privacy.ps1      # 公开发布前的隐私检查
├── tests/                     # 单元测试
└── docs/                      # 设计与使用文档
```

## 配置

`config.json`（首次运行可选，缺省即内置默认值）：

| 字段 | 默认 | 说明 |
|:--|:--|:--|
| `host` / `port` | `127.0.0.1` / `1236` | 控制面监听地址 |
| `reg_dir` | `reg` | 注册条根目录 |
| `audit_retention_days` | `7` | 登记簿事件保留天数，`0` = 永不清理 |
| `cors_local_only` | `true` | 仅本机来源网页可获得跨源许可 |
| `monitor.fast_sec` / `idle_sec` / `panel_grace_sec` | `1` / `10` / `15` | 面板可见/隐藏时的刷新间隔、活跃宽限 |
| `scheduler.tick_sec` | `10` | 定时任务检查间隔 |
| `backup.keep` | `7` | `bak/` 保留份数，`0` = 不自动清理 |

这些都能在面板 ⚙ 设置里改，保存后立即生效并写回 `config.json`。

## 安全边界

控制面只绑回环地址、跨源写操作默认拒绝、**没有账户体系**——任何本机进程本就能直接调用接口，
这是刻意的取舍。不要把 `host` 改成 `0.0.0.0` 后仍不加认证。细节与漏洞报告方式见
[SECURITY.md](SECURITY.md)。

## 文档

| 想知道什么 | 文档 |
|:--|:--|
| 模块职责、依赖、线程、数据流、状态机、身份判定 | [docs/architecture.md](docs/architecture.md) |
| 注册条字段、四种模式、健康地址选择 | [docs/design/registry.md](docs/design/registry.md) |
| 按需服务：点开即启、空闲回收 | [docs/design/on-demand.md](docs/design/on-demand.md) |
| 日志边界、登记簿事件字典 | [docs/design/logging.md](docs/design/logging.md) |
| 定时任务：协议、调度语义、行内编辑 | [docs/design/cron.md](docs/design/cron.md) |
| 面板分组、设置面板 | [docs/design/panel.md](docs/design/panel.md) |
| 控制面跨源（CORS）策略 | [docs/design/cors.md](docs/design/cors.md) |
| 日常操作、REST API、快速上手 | [docs/guide/usage.md](docs/guide/usage.md) |
| 注册新服务、接入检查单 | [docs/guide/registration.md](docs/guide/registration.md) |
| 历史排坑（冷启动慢 / 自启黑窗 / 会话绑定） | [docs/analysis/pitfalls.md](docs/analysis/pitfalls.md) |

## 开发与测试

```powershell
python -m unittest discover -s tests    # 全量单测
pwsh -File .\tools\check_privacy.ps1    # 提交前隐私检查
```

代码约定、提交规范与**公开发布规则（脱敏清单）**见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 许可

[MIT](LICENSE)
