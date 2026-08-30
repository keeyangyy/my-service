# my-service

本机服务管理框架 —— 管理其他服务的启停、自启与跳转。早期内部版本 的整体升级版。

## 定位（务必理解的三条铁律）

1. **my-service 只是框架**：`src\` 内永不出现任何服务的业务源码。
2. **注册 = 在 reg\ 放一条注册**：每个被管服务在 `reg\<服务名>.json` 有一份注册条，
   `path` 指向服务真实家（可任意盘符/目录）；没有注册条的服务与 my-service
   完全无关，互不干扰。
3. **宿主不进服务列表，但有专属自操作通道**：my-service 自身不写注册条
   （避免"编排者杀自己/自启套娃"的自举悖论），因此服务列表里永远看不到它。
   但 header 右上角提供 `⟳重启宿主 / ■停止宿主` 专用按钮（走 `/api/host/*` 独立
   端点，非服务通道）；命令行与开机则由 `entry\start|stop|restart.ps1` 和计划任务
   `StartMyService` 负责。二者都不会把 my-service 变成"自己的一个服务"。

## 目录结构

```
my-service\
├─ README.md                  # 本文档
├─ config.json                # host/port/reg_dir 等
├─ reg\                       # ★ 注册中心（唯一发现源）：被管服务的"花名册"
│   └─ <服务名>.json          # 每条注册：path=服务真实家 + 启停/健康/自启声明
├─ adapters\                  # ★ 可选转接层：<服务名>\ 下放薄适配脚本(自动优先命中)
├─ entry\                     # ★ 人的操作入口：无参/有默认值，可直接运行
│   ├─ start.ps1              # PowerShell 运行 → 启动宿主（幂等）
│   ├─ stop.ps1               # → 停止宿主（连带收割全部被管服务）
│   ├─ restart.ps1            # → 重启宿主
│   ├─ install_autostart.ps1 / uninstall_autostart.ps1  # 自启注册/注销（一次性运维）
│   └─ Dashboard.url          # 双击 → 直接打开管理面板
├─ logs\                      # 运行时产物（两档模型约定）：宿主日志 + pid
│   ├─ service.log / service.err.log               # 宿主自身日志（只有启停事件）
│   └─ service.pid
└─ src\                       # 实现细节 + 程序化接口
    ├─ server.py              # 组装入口（含 HostController 自管理）
    ├─ ctl.ps1                # 内部引擎（entry\ 入口的底层，勿直接调用）
    ├─ svc.ps1                # 服务管理 CLI（带参数：终端/AI 会话/脚本用，非双击入口）
    ├─ core\
    │   ├─ scanner.py         # 扫描 reg\*.json（注册中心唯一源）
    │   ├─ runner.py          # 进程原子操作：启动/树杀/健康检查
    │   ├─ state.py           # Manager：状态机 + monitor 秒级观测收敛
    │   └─ api.py             # REST API + web 静态托管
    └─ web\                   # Dashboard（原生 JS 零依赖）
```

## 转接层（adapters\，可选）

不是每个服务都适合/允许在自家目录放启动包装——此时把薄适配脚本放进
`my-service\adapters\<服务名>\`，框架自动优先采用：

```
my-service\adapters\<服务名>\
└── start.ps1 / stop.ps1     # 与注册条同名的相对路径自动优先命中
```

**解析优先级**：entry/stop_script 的相对路径先查
`adapters\<服务名>\<文件名>`（有文件=胜出），未命中才落到服务真实家 `path\<文件名>`。
适配器是「接入胶水」（环境注入/参数包装/调官方脚本），不是业务源码，不违反铁律 1。
空 adapters（或没有该文件）= 直接使用官方/原生脚本，无需动注册条。

## 状态机

`stopped → starting → running → stopping → stopped`，任何异常进 `failed(附原因)`。
monitor 线程每秒以健康探测校正状态并强制超时收敛——杜绝"starting 永久残留"类问题。
### 防双开（external_alive 统一判定）

启动/自启前先判"服务是否已有实例在跑"（残留、外部或手动实例）：

| 健康类型 | 判据 |
|:--|:--|
| `port` / `http` | 健康目标可达 |
| `process` | 读 `pidfile` → 进程存活（`tasklist`） |
| `none` / process 无 pidfile | 无法判定（固有限制，只能靠托管句柄） |

- **手动/冷启动**：stopped/failed 且已检测到实例在跑 → **拒绝启动**并提示"已有实例在运行"（防双重拉起）
- **自启**：已在运行 → **跳过自启并归位 running**（面板如实显示，不诱导你再点启动）；未运行才拉起
- **detached 自愈**：实例其实活着却被标 failed（如升级慢导致启动超时）→ 下一轮健康探测通过即自动恢复 running，免手动重启

> 典型场景：宿主重启时旧子进程成孤儿仍在跑、或你先手动起了服务——现在都不会双开。

## 注册条协议（reg\*.json）

```jsonc
{
  "path": "D:/program(portable)/some-service", // ★ 服务真实家（绝对路径或相对 reg\ 目录）
  "enabled": true,                 // false = 暂时停用（不参与编排）
  "display": "SomeService",
  "description": "AI agent 通信网关",
  "entry": "start.ps1",            // 相对 path；支持 .py/.ps1/.cmd/.bat/.exe
  "args": [],
  "cwd": ".",
  "mode": "managed",               // managed=托管进程树 | detached=点火器型(官方启停脚本)
                                   // detached 需 stop_script 或 pidfile 之一；health 禁 process
  "health": { "type": "http", "url": "http://localhost:8090/health", "timeout_sec": 60 },
                                   // type: port|http|process|none
  "url": "http://localhost:8090/", // 可选：服务自带网页，Dashboard 一键 ↗
  "env": {"SOME-SERVICE_NO_WAIT": "1"}, // 附加环境变量
  "stop_script": "stop.ps1",       // 可选：停止时先执行的灭火脚本（相对 path）
  "pidfile": "run/app.pid",        // 可选：pid 文件(相对 path)——process 型防双开判据
  "autostart": true                // 权威值存此，前端开关回写这里
}
```

**地址用 `localhost` 而非 `127.0.0.1`（实测教训）**：服务可能只监听 IPv6 `::`
（如 some-service 升级后），框架用 `127.0.0.1` 走 IPv4 直连会探测失败而误报超时；
`localhost` 优先解析 IPv6，更贴合实际监听面。

**`timeout_sec` 要放宽的场景**：带自升级/冷启动慢的服务（some-service 跨版本升级即超 20s），
建议 60s 起，否则会"进程其实活着、状态却超时误判 failed"。

**路径语义**：注册条里除 `path` 外的一切路径（entry/cwd/stop_script）都**相对 `path`** 解析；
框架收集的 stdout 写 `<path>\logs\service.log[.err]`，遵循 services 总目录两档约定。

**停止方式**：`managed` 默认 `taskkill /T /F` 整树收割，可另配 `stop_script` 先执行再收树；
`detached`（点火器型：启动脚本点完火即退场算正常，死活只信健康探测，停止执行官方
`stop_script`）适合自带官方启停脚本的商用/外部服务（参见 `reg\some-service.json` 样例）。

## 注册服务：最小要求与步骤

**最小要求（不满足则拒收，注册条标 invalid 并给原因）**：
- 启动入口：`start.ps1` 或等价脚本（无参数、可直接运行拉起常驻进程）
- 停止方式：`stop.ps1` 或等价停止命令（或可被 taskkill /T 终结）
- 健康判定：HTTP 端口（建议 `/health`）、port、process 三选一可探测

**注册步骤**：
1. 服务在自己真实家补齐上述三件（商业软件通常已自带官方脚本）
2. 在 `my-service\reg\` 新建 `<服务名>.json` 注册条（字段见上）
3. 无需重启宿主：刷新 Dashboard（或 `.\src\svc.ps1 ls`）即出现在列表；
   `autostart: true` 则宿主启动时自动拉起（若已在运行会自动跳过并归位，不双开）

**接入检查单**：列表出现但状态不对时按序查——注册条无效（红标 invalid 附原因）→
端口冲突（健康端口被占用/假健康防护拒绝）→ 入口文件不存在 → 健康超时
（`timeout_sec` 内未达健康判定，日志面板看启动输出）。

failed 提示「检测到已有实例在运行」= **正常防护**，说明服务其实活着（外部/残留实例），
按需去对方那边停止，或直接点面板重启走框架原子切换；提示「健康目标已被占用」同理。

### 完整实例：接入一个自带官方启停脚本的商业软件（some-service）

真实文件 `reg\some-service.json`（detached 型、零副本单处维护）：

```json
{
  "path": "D:/program(portable)/some-service",
  "enabled": true,
  "display": "SomeService",
  "description": "AI agent 通信网关（8090）——单处维护：直指官方脚本",
  "entry": "start.ps1",
  "args": [],
  "cwd": ".",
  "mode": "detached",
  "health": {"type": "http", "url": "http://localhost:8090/health", "timeout_sec": 60},
  "url": "http://localhost:8090/",
  "env": {"SOME-SERVICE_NO_WAIT": "1"},
  "stop_script": "stop.ps1",
  "autostart": true
}
```

要点回顾：`path`=便携目录（**exe 与数据分离的服务，cwd 钉死在此防漂移空库**）、
entry/stop_script 直指官方脚本（`adapters\` 留空=不劫持）、`env` 关掉官方脚本交互等待。

## 用户操作接口

| 层 | 入口 |
|:--|:--|
| Web Dashboard | http://127.0.0.1:1236 （一行一服务：状态灯/启停/↗跳转/日志窗/自启开关；header 右上含宿主运行时长 + 重启/停止按钮） |
| 用户操作入口 | `entry\start.ps1`、`entry\stop.ps1`、`entry\restart.ps1`（PowerShell 运行；幂等）、`entry\Dashboard.url`（双击开面板） |
| 服务 CLI | `.\src\svc.ps1 ls\|log <n>\|start\|stop\|restart <name>`（终端/AI 会话用） |
| REST API | `GET /api/services`、`GET /api/host`(宿主 pid/uptime)、`GET /api/logs/{svc}?n=`、`GET /api/host-log?n=`、`POST /api/services/{svc}/start\|stop\|restart`、`POST /api/config/{svc}`、`POST /api/host/stop\|restart` |
| 注册服务 | 在 `my-service\reg\` 放 `<服务名>.json`；注销 = 删除注册条或 `"enabled": false` |

## 快速上手

```powershell
cd C:\path\to\my-service
# 图形入口：直接打开 entry\ 文件夹，运行 start.ps1，或双击 Dashboard.url
pwsh -File .\entry\start.ps1             # 命令行起宿主
start http://127.0.0.1:1236              # 打开面板
.\entry\install_autostart.ps1          # 登录自启（一次性，需 gsudo 提权）
```