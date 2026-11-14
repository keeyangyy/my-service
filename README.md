# my-service

本机服务管理框架 —— 管理其他服务的启停、自启与跳转。早期内部版本 的整体升级版。

## 定位（三条铁律）

1. **my-service 只是框架**：`src/` 内永不出现任何服务的业务源码。
2. **注册 = 在 `reg/` 放一条注册**：每个被管服务在 `reg/<服务名>.json` 有一份注册条，`path` 指向服务真实家（可任意盘符/目录）；没有注册条的服务与 my-service 完全无关，互不干扰。
3. **宿主不进服务列表，但有专属自操作通道**：my-service 自身不写注册条（避免"编排者杀自己 / 自启套娃"的自举悖论），服务列表里永远看不到它。header 右上角提供 `⟳重启宿主 / ■停止宿主` 专用按钮（走 `/api/host/*` 独立端点）；命令行与开机由 `run/start|stop|restart.ps1` 和自启项负责。

## 目录结构

```
my-service/
├── README.md
├── config.json                # host / port / reg_dir / 保留天数 / CORS 开关
├── reg/                       # ★ 注册中心（唯一发现源）
│   ├── <服务名>.json          #   服务注册条
│   └── cron/<任务名>.json     #   定时任务注册条
├── adapters/                  # 可选转接层：<服务名>/ 下放薄适配脚本，自动优先命中
├── run/                       # ★ 人的入口
│   ├── start.ps1 / stop.ps1 / restart.ps1
│   ├── install_autostart.ps1 / uninstall_autostart.ps1
│   └── Dashboard.url
├── logs/                      # 记录：host.jsonl 登记簿 / host.err.log（不入库）
├── data/                      # 数据：host.pid / pids / cron_state（不入库）
├── bak/                       # 实例配置备份：reg/ + config.json + adapters/（不入库）
├── src/
│   ├── server.py              # 组装入口（config → Scanner → Manager → Scheduler → HTTP）
│   ├── ctl.ps1                # 内部引擎（run/ 的底层，勿直接调用）
│   ├── svc.ps1                # 服务管理 CLI（终端 / AI 会话用）
│   └── core/
│       ├── host.py            # 宿主自重启 / 停止（脱离服务通道）
│       ├── api/               # HTTP：__init__(路由) · cors.py · static.py
│       ├── state/             # 状态机：manager / monitor
│       ├── scheduler.py       # 定时任务
│       ├── runner.py          # 进程与健康
│       ├── scanner.py         # 注册条解析
│       └── audit.py           # 登记簿
├── web/                       # 前端静态：index.html / app.js / style.css / opening.html
├── tests/                     # 单元测试
└── docs/                      # 长期知识（见下表）
```

## 文档

| 想知道什么 | 文档 |
|:--|:--|
| 模块职责、依赖、线程、数据流、状态机、身份判定 | [docs/architecture.md](docs/architecture.md) |
| 注册条字段、模式（managed/detached/on_demand）、健康地址选择 | [docs/design/registry.md](docs/design/registry.md) |
| 按需服务：点开即启、空闲回收 | [docs/design/on-demand.md](docs/design/on-demand.md) |
| 日志边界、登记簿事件字典 | [docs/design/logging.md](docs/design/logging.md) |
| 定时任务：协议、调度语义、行内编辑 | [docs/design/cron.md](docs/design/cron.md) |
| 面板分组、设置面板、服务与任务关联 | [docs/design/panel.md](docs/design/panel.md) |
| 控制面跨源（CORS）策略 | [docs/design/cors.md](docs/design/cors.md) |
| 日常操作、REST API、快速上手 | [docs/guide/usage.md](docs/guide/usage.md) |
| 注册新服务、接入检查单 | [docs/guide/registration.md](docs/guide/registration.md) |
| 历史排坑（冷启动慢 / 自启黑窗 / 会话绑定） | [docs/analysis/pitfalls.md](docs/analysis/pitfalls.md) |

## 快速上手

```powershell
cd C:\path\to\my-service
pwsh -File .\run\start.ps1               # 命令行起宿主
start http://127.0.0.1:1236              # 打开面板（或双击 run\Dashboard.url）
.\run\install_autostart.ps1              # 安装自启（默认启动文件夹方案）
```

## 注册一个服务（三步）

1. 服务家补齐启动入口、停止方式、健康判定。
2. 在 `reg/` 放 `<服务名>.json`。
3. 刷新面板即出现；无需重启宿主。

完整字段与检查单见 [docs/guide/registration.md](docs/guide/registration.md)。
