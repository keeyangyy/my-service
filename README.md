# my-service

**把散落在各处的本地服务，收进一个网页控制台。**

一条 JSON 注册一个服务，之后统一启停、开机自启、健康探测、定时调度——
不用再记每个服务各自的启动命令，也不用同时开几个终端窗口。

零第三方依赖（Python 标准库 + 原生 JS），控制面只监听本机回环。

![控制台主面板](docs/guide/images/panel.png)

## 解决什么问题

| 痛点 | 做法 |
|:--|:--|
| 服务散在各处，每个一套启停命令 | `reg/services/<名称>.json` 放一条 JSON 就接入，面板与 CLI 统一操作 |
| 忘记关、端口被占、卡在「启动中」 | 健康探测 + 状态机收敛；按需服务空闲自动回收 |
| 开机要手动一个个点 | 面板顶部一键开机自启（启动文件夹 `.lnk` → pythonw，无窗口） |
| 不知道谁在跑、什么时候起的 | 结构化事件流 `data/logs/host.jsonl`，面板「事件」直接看 |

## 特性

| 能力 | 说明 |
|:--|:--|
| 注册即接入 | `reg/services/<名称>.json` 一个文件就是一个服务，改完刷新面板即生效，不用重启宿主 |
| 四种形态 | `managed` 常驻托管、`detached` 只管外部进程、`on_demand` 点开即启+空闲回收、`task` 定时跑一次 |
| 健康判定 | `http` 探测地址或 `port` 端口探测；失败按状态机收敛为 `failed`，不会永久卡在 `starting` |
| 定时任务 | `reg/tasks/<名称>.json` 声明 `daily` / `interval` 等调度，宿主内调度器到点执行 |
| 面板 | 分组卡片、拖拽排序、服务/任务详情、日志查看、顶部自启与更新开关、宿主自身重启与停止 |
| 备份与保留 | 一键备份 `reg/` + `config.json` + `adapters/` 到 `data/bak/`，只留最近 N 份（默认 7） |
| 跨源边界 | 默认只给本机来源的网页回 CORS 头，跨源写操作一律拒绝 |
| 便携分发 | 打包只裁切运行必需文件（约 0.5 MB），目标机器装 Python 即可跑 |

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

想先看效果，直接跑仓库自带的 demo：把源码复制到临时目录、用独立端口（1246）起实例，
不动你本机的 `reg/` 与数据。

```powershell
pwsh -File .\examples\demo\start-demo.ps1          # 起在 http://127.0.0.1:1246
pwsh -File .\examples\demo\start-demo.ps1 -Stop    # 停掉并清理
```

只想照着写注册条，看下面的「注册第一个服务」或 demo 里的 `examples\demo\reg\` 即可。

常驻 / 停止：

```powershell
pwsh -File .\run\stop.ps1
pwsh -File .\run\restart.ps1
.\run\install_autostart.ps1         # 安装开机自启（启动文件夹 + .lnk，无窗口）
```

## 便携包（裁切打包）

```powershell
pwsh -File .\tools\build-portable.ps1      # 产物：dist\my-service-<版本>.zip（约 0.5 MB）
```

只打运行必需的文件，依赖目标机器的 Python 3.10+。解压后双击 `run\start.ps1` 启动、`run\stop.ps1` 停止。包根的结构与仓库一致：

```
my-service-x.y.z/
├── src/  web/  run/          # 代码与入口（双击 run\start.ps1 启停）
├── docs/guide/               # 使用说明（使用指南 / 注册指南 / 面板截图）
├── README.md CHANGELOG.md LICENSE
├── config.json               # 数据根配置
├── reg/                      # 注册条（你手写；升级保留）
├── data/                     # 运行时数据（升级保留）
│   ├── logs/                 #   记录：只追加，随时可删
│   ├── bak/                  #   备份（reg + config.json）
│   └── update/               #   升级暂存
└── VERSION.txt               # 打包标记（也是「支持一键升级」的判据）
```

- 依赖：目标机器需要 **Python 3.10+**；要连解释器一起分发就加 `-WithRuntime`（包根多一个 `runtime/`，约 +11 MB）
- 首次启动会在 `run\` 生成 `Dashboard.url`
- 开机自启：面板顶部「自启」开关，或 `run\install_autostart.ps1`（`.lnk` 指向 `src\server.py`，升级替换代码目录后不用重装）
- 升级：面板「检查更新」→ 点更新 → 自动下载并重启，重启瞬间按清单替换 `src/ web/ run/ docs/` 与三个文档；旧的留在 `data/bak/code-prev/` 可回滚，`reg/ data/ config.json` 一律不动
- 开发者向文档（架构、设计、排坑）只留在仓库，不进包
- 自定义数据位置：设置环境变量 `MY_SERVICE_HOME` 指向任意目录

## 注册第一个服务（三步）

1. 服务目录里补齐三件套：**启动入口**、**停止方式**、**健康判定**。
2. 在 `reg\services\` 放一条注册条。
3. 刷新面板 —— 服务出现，无需重启宿主。

最小注册条（完整字段见 [docs/design/registry.md](https://github.com/keeyangyy/my-service/blob/master/docs/design/registry.md)）：

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

放在 `reg\tasks\<名称>.json`，调度类型与参数见 [docs/design/cron.md](https://github.com/keeyangyy/my-service/blob/master/docs/design/cron.md)；
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
├── run/                       # 人的入口（双击 start/stop）
│   ├── start.ps1 / stop.ps1 / restart.ps1
│   ├── install_autostart.ps1 / uninstall_autostart.ps1
│   └── Dashboard.url          # 首次启动时按本机配置生成（不入库）
├── data/                      # 运行时数据（整目录不入库，本项目约定）
│   ├── logs/                  #   记录：host.jsonl 登记簿、host.err.log 异常栈
│   ├── bak/                   #   备份：实例配置 + 升级前的旧代码（code-prev）
│   ├── update/                #   升级暂存：code.new / pending.json
│   └── pids/ host.pid cron_state.json entry-out/
├── src/
│   ├── server.py              # 组装入口（config → Scanner → Manager → Scheduler → HTTP）
│   ├── service_cli.py         # 启停引擎（run/*.ps1 的底层，含升级落地）
│   ├── svc.ps1                # 终端 / AI 会话用的服务管理 CLI
│   └── core/                  # api / state / scheduler / runner / scanner / audit / backup
├── web/                       # 前端静态资源（index.html / app.js / style.css / opening.html）
├── tools/
│   ├── build-portable.ps1     # 便携包构建（裁切组装 + zip；-WithRuntime 可带解释器）
│   └── check_privacy.ps1      # 公开发布前的隐私检查
├── .github/workflows/
│   ├── ci.yml                 # 检查：ruff / 单测 / 覆盖率 / 前端语法（PR 与发版共用）
│   └── release.yml            # 发版：校验 → 检查 → 打包 → 建 tag 与 Release → 自检
├── examples/                  # 示例注册条 + 可运行 demo（examples/demo）
├── tests/                     # 单元测试
├── docs/                      # 文档
│   ├── guide/                 # 用户向：使用指南 / 注册指南（+ images/ 面板截图，进便携包）
│   ├── design/                # 开发者向：注册条 / 定时任务 / 日志 / CORS 设计
│   ├── analysis/              # 排坑记录：历史踩坑与方案演进
│   └── architecture.md        # 模块职责与数据流
├── AGENTS.md                  # 项目规则入口（框架边界 / 提交与发版规范）
├── CONTRIBUTING.md            # 贡献指南与公开发布规则（脱敏清单）
├── SECURITY.md                # 威胁模型与漏洞报告
└── LICENSE                    # MIT 许可证
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
| `backup.keep` | `7` | `data/bak/` 保留份数，`0` = 不自动清理 |

这些都能在面板 ⚙ 设置里改，保存后立即生效并写回 `config.json`。

## 安全边界

控制面只绑回环地址、跨源写操作默认拒绝、不设账户体系——本机进程本就能直接调用接口，
这是刻意的取舍。不要把 `host` 改成 `0.0.0.0` 后仍不加认证。细节与漏洞报告方式见
[SECURITY.md](https://github.com/keeyangyy/my-service/blob/master/SECURITY.md)。

## 文档

| 想知道什么 | 文档 |
|:--|:--|
| 模块职责、依赖、线程、数据流、状态机、身份判定 | [docs/architecture.md](https://github.com/keeyangyy/my-service/blob/master/docs/architecture.md) |
| 注册条字段、四种模式、健康地址选择 | [docs/design/registry.md](https://github.com/keeyangyy/my-service/blob/master/docs/design/registry.md) |
| 按需服务：点开即启、空闲回收 | [docs/design/on-demand.md](https://github.com/keeyangyy/my-service/blob/master/docs/design/on-demand.md) |
| 日志边界、登记簿事件字典 | [docs/design/logging.md](https://github.com/keeyangyy/my-service/blob/master/docs/design/logging.md) |
| 定时任务：协议、调度语义、行内编辑 | [docs/design/cron.md](https://github.com/keeyangyy/my-service/blob/master/docs/design/cron.md) |
| 面板分组、设置面板 | [docs/design/panel.md](https://github.com/keeyangyy/my-service/blob/master/docs/design/panel.md) |
| 控制面跨源（CORS）策略 | [docs/design/cors.md](https://github.com/keeyangyy/my-service/blob/master/docs/design/cors.md) |
| 控制面 REST API：接口清单、错误格式、来源标记 | [docs/design/api.md](https://github.com/keeyangyy/my-service/blob/master/docs/design/api.md) |
| 日常操作、常用接口、快速上手 | [docs/guide/usage.md](docs/guide/usage.md) |
| 注册新服务、接入检查单 | [docs/guide/registration.md](docs/guide/registration.md) |
| 历史排坑（冷启动慢 / 自启黑窗 / 会话绑定） | [docs/analysis/pitfalls.md](https://github.com/keeyangyy/my-service/blob/master/docs/analysis/pitfalls.md) |
| 项目规则、提交与发版规范 | [AGENTS.md](https://github.com/keeyangyy/my-service/blob/master/AGENTS.md) |
| 跑 demo 体验完整链路 | [examples/demo/README.md](https://github.com/keeyangyy/my-service/blob/master/examples/demo/README.md) |

## 开发与测试

```powershell
python -m unittest discover -s tests    # 全量单测
pwsh -File .\tools\check_privacy.ps1    # 提交前隐私检查
```

项目规则（框架边界、运行态约定、改动流程）见 [AGENTS.md](https://github.com/keeyangyy/my-service/blob/master/AGENTS.md)；
代码约定、提交规范与**公开发布规则（脱敏清单）**见 [CONTRIBUTING.md](https://github.com/keeyangyy/my-service/blob/master/CONTRIBUTING.md)。

## 许可

[MIT](LICENSE)
