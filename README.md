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

## 下载即用

**不需要源码，也不需要克隆仓库。**

1. 打开 [最新版本](https://github.com/keeyangyy/my-service/releases/latest)，下载 `my-service-<版本>.zip`。
2. 解压到任意目录，比如 `D:/apps/my-service`（路径别选需要管理员权限的位置）。
3. 启动：进入 `run\` 文件夹，**右键 `start.ps1` → 使用 PowerShell 运行**。
   （Windows 默认双击 `.ps1` 是打开编辑器，不会执行。）
   也可以用终端：`pwsh -File run\start.ps1`。
4. 打开 <http://127.0.0.1:1236>；首次启动后 `run\` 下还会生成 `Dashboard.url`，双击即可开面板。

运行要求：**Windows 10 / 11** + **Python 3.10+**（安装时勾选 Add Python to PATH）。
当前发布包不自带解释器，目标机器需要能执行 `python`。

停止与开机自启：

```powershell
pwsh -File .\run\stop.ps1                  # 停止宿主（被管服务不受影响）
pwsh -File .\run\restart.ps1               # 重启宿主
.\run\install_autostart.ps1                # 开机自启（面板顶部「自启」开关等价）
.\run\uninstall_autostart.ps1              # 取消自启
```

升级：面板顶部 **⟳ 更新** → 检查到新版本后按钮变成 **⬆ 升级到 vX** → 点一下自动下载、
替换 `src/ web/ run/ docs/` 并重启。`reg/`、`data/`、`config.json` 不动；旧代码留在
`data/bak/code-prev/` 可回滚。

## 用起来

### 面板

打开 <http://127.0.0.1:1236> 就是全部操作入口：

| 位置 | 能做什么 |
|:--|:--|
| 顶部栏 | 开机自启开关、检查更新、编排器设置、事件登记簿、重启宿主、停止宿主 |
| 服务卡片 | 启动 / 停止、⚙ 设置（空闲回收时长、关联定时任务）、事件、点卡片打开服务页面 |
| 定时任务行 | 启用开关、▶ 立即运行、⚙ 设置（时刻 / 间隔 / 超时 / 补跑）、事件 |
| 分组 | 常驻服务与即用即走分两组，按住把手可拖拽排序，顺序会保存 |

### 接入第一个服务（三步）

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

### 定时任务

放在 `reg\tasks\<名称>.json`，调度类型与参数见
[docs/design/cron.md](https://github.com/keeyangyy/my-service/blob/master/docs/design/cron.md)；
面板里可直接改时刻、看上次结果、手动「▶ 立即运行」。

### 命令行

```powershell
.\src\svc.ps1 ls                    # 服务列表与状态
.\src\svc.ps1 start <名>            # 启停单个服务
.\src\svc.ps1 stop <名>
.\src\svc.ps1 crons                 # 定时任务列表
.\src\svc.ps1 cron-run <名>         # 手动跑一次任务
.\src\svc.ps1 events [名] [条数]    # 看启停登记簿
```

### 数据在哪、哪些能删

| 路径 | 性质 | 删了会怎样 |
|:--|:--|:--|
| `reg\services\`、`reg\tasks\` | 你手写的注册条 | 服务 / 任务从面板消失 |
| `data\logs\` | 记录（登记簿、异常栈） | 无影响，下次启动重建 |
| `data\bak\` | 备份（注册条 + 配置、升级前的旧代码） | 失去回滚点 |
| `data\update\` | 升级暂存 | 未生效的升级丢失，重新下载即可 |
| `data\cron_state.json` | 定时任务状态 | 任务重算下次触发时间 |

`reg\` 是事实源，删了要重写；`data\` 可整体删除，代价是丢定时任务状态与备份。
想把数据放到别处，设置环境变量 `MY_SERVICE_HOME` 指向目标目录。

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
│   ├── build-portable.ps1     # 发版打包（裁切组装 + zip）
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

`config.json`（缺省即内置默认值，面板 ⚙ 设置也能改）：

| 字段 | 默认 | 说明 |
|:--|:--|:--|
| `host` / `port` | `127.0.0.1` / `1236` | 控制面监听地址 |
| `reg_dir` | `reg` | 注册条根目录 |
| `audit_retention_days` | `7` | 登记簿事件保留天数，`0` = 永不清理 |
| `cors_local_only` | `true` | 仅本机来源网页可获得跨源许可 |
| `monitor.fast_sec` / `idle_sec` / `panel_grace_sec` | `1` / `10` / `15` | 面板可见/隐藏时的刷新间隔、活跃宽限 |
| `scheduler.tick_sec` | `10` | 定时任务检查间隔 |
| `backup.keep` | `7` | `data/bak/` 保留份数，`0` = 不自动清理 |

## 安全边界

控制面只绑回环地址、跨源写操作默认拒绝、不设账户体系——本机进程本就能直接调用接口，
这是刻意的取舍。不要把 `host` 改成 `0.0.0.0` 后仍不加认证。细节与漏洞报告方式见
[SECURITY.md](https://github.com/keeyangyy/my-service/blob/master/SECURITY.md)。

## 从源码运行（开发）

```powershell
git clone https://github.com/keeyangyy/my-service.git
cd my-service
pwsh -File .\run\start.ps1        # 启动宿主（127.0.0.1:1236）
python -m unittest discover -s tests
```

源码工作区不支持面板一键升级（会提示改用 `git pull`），这是刻意区分：升级只对下载的发行包生效。

想先看效果而不动本机数据，跑自带 demo——把源码复制到临时目录、用独立端口 1246 起实例：

```powershell
pwsh -File .\examples\demo\start-demo.ps1          # 起在 http://127.0.0.1:1246
pwsh -File .\examples\demo\start-demo.ps1 -Stop    # 停掉并清理
```

## 打包与发版（维护者）

打包由 CI 完成，日常不需要本地操作；手动打一个包用于验证时：

```powershell
pwsh -File .\tools\build-portable.ps1                 # 产物：dist\my-service-<版本>.zip
pwsh -File .\tools\build-portable.ps1 -WithRuntime    # 连 Python 解释器一起打（约 +11 MB）
```

包里只放运行必需的文件（`src/ web/ run/ docs/guide/` + 三个文档 + `reg/`、`data/` 骨架），
包根就是代码根，版本号写在 `VERSION.txt`（也是一键升级的判据）。

发版由 `.github/workflows/release.yml` 执行：填版本号触发 → 校验版本与 CHANGELOG →
复用 `ci.yml` 全量检查 → 打包 → 建 tag 与 Release → 上传 zip 与 `latest.json` → 发布后自检。
失败会自动删掉 tag 与 Release。完整流程见 [AGENTS.md](https://github.com/keeyangyy/my-service/blob/master/AGENTS.md)。

## 文档

| 想知道什么 | 文档 |
|:--|:--|
| 日常操作、终端用法、哪些目录能删 | [docs/guide/usage.md](docs/guide/usage.md) |
| 注册新服务、接入检查单 | [docs/guide/registration.md](docs/guide/registration.md) |
| 模块职责、依赖、线程、数据流、状态机、身份判定 | [docs/architecture.md](https://github.com/keeyangyy/my-service/blob/master/docs/architecture.md) |
| 注册条字段、四种模式、健康地址选择 | [docs/design/registry.md](https://github.com/keeyangyy/my-service/blob/master/docs/design/registry.md) |
| 按需服务：点开即启、空闲回收 | [docs/design/on-demand.md](https://github.com/keeyangyy/my-service/blob/master/docs/design/on-demand.md) |
| 日志边界、登记簿事件字典 | [docs/design/logging.md](https://github.com/keeyangyy/my-service/blob/master/docs/design/logging.md) |
| 定时任务：协议、调度语义、行内编辑 | [docs/design/cron.md](https://github.com/keeyangyy/my-service/blob/master/docs/design/cron.md) |
| 面板分组、设置面板 | [docs/design/panel.md](https://github.com/keeyangyy/my-service/blob/master/docs/design/panel.md) |
| 控制面跨源（CORS）策略 | [docs/design/cors.md](https://github.com/keeyangyy/my-service/blob/master/docs/design/cors.md) |
| 控制面 REST API：接口清单、错误格式、来源标记 | [docs/design/api.md](https://github.com/keeyangyy/my-service/blob/master/docs/design/api.md) |
| 历史排坑（冷启动慢 / 自启黑窗 / 会话绑定） | [docs/analysis/pitfalls.md](https://github.com/keeyangyy/my-service/blob/master/docs/analysis/pitfalls.md) |
| 项目规则、提交与发版规范 | [AGENTS.md](https://github.com/keeyangyy/my-service/blob/master/AGENTS.md) |
| 跑 demo 体验完整链路 | [examples/demo/README.md](https://github.com/keeyangyy/my-service/blob/master/examples/demo/README.md) |

代码约定、提交规范与**公开发布规则（脱敏清单）**见
[CONTRIBUTING.md](https://github.com/keeyangyy/my-service/blob/master/CONTRIBUTING.md)。

## 许可

[MIT](LICENSE)
