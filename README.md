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
│   ├─ <服务名>.json          # 每条注册：path=服务真实家 + 启停/健康/自启声明
│   └─ cron\                  # ★ 定时任务注册（第二物种，见「定时任务」章节）
│       └─ <任务名>.json      # 到点跑一次脚本，无常驻进程
├─ adapters\                  # ★ 可选转接层：<服务名>\ 下放薄适配脚本(自动优先命中)
├─ entry\                     # ★ 人的操作入口：无参/有默认值，可直接运行
│   ├─ start.ps1              # PowerShell 运行 → 启动宿主（幂等）
│   ├─ stop.ps1               # → 停止宿主（被管服务不受影响，继续运行）
│   ├─ restart.ps1            # → 重启宿主
│   ├─ install_autostart.ps1 / uninstall_autostart.ps1  # 自启安装/卸载（默认 Startup 启动文件夹方案）
│   └─ Dashboard.url          # 双击 → 直接打开管理面板
├─ logs\                      # ★ 框架侧运行时产物（唯一作者 = my-service）
│   ├─ host.jsonl             # 启停登记簿：一行一事件（详见「日志边界」）
│   ├─ host.err.log           # 宿主自身异常栈（sys.stderr）
│   ├─ host.pid               # 宿主 pid（ctl.ps1 优先读它）
│   ├─ cron_state.json        # 定时任务账本：last_run/next_run 等（scheduler 独写）
│   └─ pids\<服务名>.pid       # 被管服务 pid——框架记账留在框架家
└─ src\                       # 实现细节 + 程序化接口
    ├─ server.py              # 组装入口（含 HostController 自管理）
    ├─ ctl.ps1                # 内部引擎（entry\ 入口的底层，勿直接调用）
    ├─ svc.ps1                # 服务管理 CLI（带参数：终端/AI 会话/脚本用，非双击入口）
    ├─ core\
    │   ├─ scanner.py         # 扫描 reg\*.json（注册中心唯一源）
    │   ├─ audit.py           # 启停登记簿（host.jsonl 的唯一作者）
    │   ├─ runner.py          # 进程原子操作：启动/树杀/健康检查/一次性执行（输出不接管）
    │   ├─ scheduler.py       # 定时任务调度器（reg\cron\ 唯一源，与 monitor 并列线程）
    │   ├─ state.py           # Manager：状态机 + monitor 秒级观测收敛
    │   └─ api.py             # REST API + web 静态托管
    ├─ web\                   # Dashboard + 启动中转页（原生 JS 零依赖）
    │   ├─ index.html / app.js / style.css  # 管理面板
    │   └─ opening.html       # 点 ↗ 的中转页（秒开转圈 → 启动 → 就绪后跳转）
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

## 日志边界（三条流，一个文件只有一个作者）

**框架不写服务日志，也不读。** 服务加不加进 my-service，它的日志形态一字不变；
my-service 只在自己家记一本"谁、何时、启停了什么、成没成"的登记簿。

| 流 | 文件 | 唯一作者 | 内容 |
|:--|:--|:--|:--|
| 控制面 | `my-service\logs\host.jsonl` | my-service | 启停生命周期事件，一行一条 |
| 宿主自身 | `my-service\logs\host.err.log` | my-service | 宿主未捕获异常栈 |
| 数据面 | `<服务 path>\logs\service.log` / `.err.log` | 服务本人 | 业务日志，格式与轮转自定 |

被管进程的 stdout/stderr **一律 DEVNULL**（不接管 = 不成为第二个作者）。
代价与补偿：服务自己 `print` 的启动报错不再留档，所以**框架能观测到的失败原因全部
落进登记簿的 `note`**（健康超时 / 端口占用 / 拒绝双开 / 退出码 / 入口不存在）。
接入前提因此更硬一条：常驻服务必须自己写文件日志（见 `C:\path\to\README.md`「日志合规」）。

### 登记簿事件字典（`host.jsonl`）

固定 7 字段：`ts`（本地时间·毫秒）/ `svc`（宿主事件为 null）/ `act` / `ok` / `dur_ms` / `pid` / `note`。

| `act` | 何时出现 |
|:--|:--|
| `host_start` / `host_stop` | 宿主起来 / 收工（含面板「重启·停止宿主」） |
| `start` | `starting` 收敛为终态：成功带耗时与 pid，失败 `note` 给原因（含 `via` 来源） |
| `stop` | 一次停止完成（用户停止、空闲回收会带各自 reason） |
| `autostart_skip` | 自启时实例已在运行 → 跳过并归位 |
| `adopt_external` | 检测到外部/手动实例，归位 running |
| `health_flip` | running 期健康翻转（失联归位 stopped / 假失败自愈） |
| `idle_reclaim` | on_demand 空闲超时自动回收 |
| `crash` | 托管进程意外退出（含 rc） |
| `spawn_fail` | 进程根本没能起来（入口异常 / 拒绝访问） |
| `reject_double` | 防双开拒绝启动 |
| `spec_invalid` | 注册条被判无效或被移除 |
| `cron_run` | 定时任务执行一轮：成功带耗时；失败 `note` 给 rc/超时/启动原因 |
| `cron_skip` | 定时任务错过计划时刻且 catch_up=false，顺延至下一周期 |

`restart` 不单独成事件，表现为 `stop → start` 两条。动作枚举以 `src\core\audit.py`
的 `ACTS` 为准，新增须同步前端 `app.js` 的 `ACT_MAP`。

查看途径：面板「▤ 登记簿」（全局）/ 卡片「事件」（单服务）；
命令行 `.\src\svc.ps1 events [name] [n]`；接口 `GET /api/audit?n=&svc=&fail=1`。

### 本次重构明确不做（不是遗漏）

- **detached 商业软件的本体日志留在程序自己的目录**：some-service 的真日志是
  `%LOCALAPPDATA%\SomeService\some-service.log`（已 38 MB 无轮转），框架既不代管也不代理读取，
  要看请自己开文件；给它加轮转属于该软件自己的合规义务。
- 官方 `some-service\start.ps1` 把本体输出重定向到 TEMP 随机文件后再 `Remove-Item` 删掉，
  那份输出注定丢失——不是 my-service 能修的事。
- 服务的裸 `print` 不落文件就是没有日志：框架不再兜这个底（历史上正是"框架代笔
  `service.log`"与服务自写抢同一个文件，才造成混码与归属不清）。

## 注册条协议（reg\*.json）

```jsonc
{
  "path": "C:/path/to/your-service",              // ★ 服务真实家（绝对路径或相对 reg\ 目录）
  "enabled": true,                 // false = 暂时停用（不参与编排）
  "display": "SomeService",
  "description": "AI agent 通信网关",
  "entry": "start.ps1",            // 相对 path；支持 .py/.ps1/.cmd/.bat/.exe
  "args": [],
  "cwd": ".",
  "mode": "managed",               // managed=托管进程树 | detached=点火器型(官方启停脚本)
                                   // on_demand=按需（点开即启、空闲自动回收，需 health.type=http）
                                   // detached 需 stop_script 或 pidfile 之一；health 禁 process
  "idle_timeout_sec": 600,         // 可选，仅 on_demand：最后业务请求起、认为"用完"的空闲秒数
  "health": { "type": "http", "url": "http://localhost:8090/health", "timeout_sec": 60 },
                                   // type: port|http|process|none
  "url": "http://localhost:8090/", // 可选：服务自带网页，Dashboard 一键 ↗
  "env": {"SOME-SERVICE_NO_WAIT": "1"}, // 附加环境变量
  "stop_script": "stop.ps1",       // 可选：停止时先执行的灭火脚本（相对 path）
  "pidfile": "run/app.pid",        // 可选：pid 文件(相对 path)——process 型防双开判据
  "autostart": true                // 权威值存此，前端开关回写这里
}
```

**健康 `url` 的 host 按服务实际监听的地址栈选（实测教训，双向）**：
- 服务只监听 IPv6 `::`（如 some-service 升级后）→ 用 `localhost`（优先解析 IPv6）；
  用 `127.0.0.1` 会走 IPv4 直连失败而误报超时
- 服务只监听 IPv4 `0.0.0.0`/`127.0.0.1`（如 your-service）→ 用 `127.0.0.1`；
  用 `localhost` 会让探测先敲一个它根本没监听的 IPv6 空门，白白多等一次超时
- 判断依据：`Get-NetTCPConnection -LocalPort <port> -State Listen` 看 LocalAddress 是 `::` 还是 `127.0.0.1`

**`timeout_sec` 要放宽的场景**：带自升级/冷启动慢的服务（some-service 跨版本升级即超 20s），
建议 60s 起，否则会"进程其实活着、状态却超时误判 failed"。

**路径语义**：注册条里除 `path` 外的一切路径（entry/cwd/stop_script）都**相对 `path`** 解析。
框架**不向服务目录写任何东西**：进程 stdout/stderr 一律 DEVNULL，服务日志由服务自管
（约定见 `C:\path\to\README.md`「日志合规」）；被管进程的 pid 记账写
`my-service\logs\pids\<服务名>.pid`，停止时由框架清理。

**停止方式**：`managed` 默认 `taskkill /T /F` 整树收割，可另配 `stop_script` 先执行再收树；
`detached`（点火器型：启动脚本点完火即退场算正常，死活只信健康探测，停止执行官方
`stop_script`）适合自带官方启停脚本的商用/外部服务（参见 `reg\some-service.json` 样例）。

### 按需服务（`on_demand`）——网页类服务不再常驻

看一眼就走的服务（如查询仪表盘）不应整天占着进程。`mode: "on_demand"` 提供：

- **点开即启**：Dashboard 点 ↗（或整行点击）先秒开宿主的 `web\opening.html` 启动中转页
  （转圈 + 文案，绝不白屏），中转页发起 start、直连探测目标端口，就绪后自动跳进服务网页
  （已在运行则几乎零等待直接跳；启动失败/超时会显示原因 + 重试/返回面板按钮）
- **空闲自动回收**：框架 monitor 每 5 秒健康复检时解析 `/health` 响应 JSON 里的
  `last_active`（秒）字段——**服务自报"最近一次业务请求"时间**（框架自身的健康探测
  不计入，否则永远判不死）；空闲超过 `idle_timeout_sec`（默认 600 秒）→
  走正规停止流程（先 `stop_script` 优雅停，再树杀兜底）→ 状态干净回到 stopped，无 failed 噪音
- **业务请求续命**：用户在网页上的操作（刷新查询、改 key 等）都会推迟回收

**约束（违反标 invalid）**：`health.type` 必须 `http`；`autostart` 必须 `false`
（按需服务由点击拉起，不随宿主运行）。`/health` 不带 `last_active` 字段时
自动回收失效，退化为"仅点开即启"。实现样例见 `reg\your-service.json`
与其 `server.py` 的 `last_active` 处理。

## 定时任务（reg\cron\）——第二物种：到点跑一次脚本

不是所有"服务"都需要常驻：签到、备份、清理、定期拉取……这类一次性脚本
用 `reg\cron\<任务名>.json` 注册，由宿主内置调度线程到点执行，**无常驻进程、
无健康检查、无状态机**。`reg` 的扫描不递归，`cron\` 子目录与服务注册天然隔离，
不会混进服务列表。

**注册条协议**：

```jsonc
{
  "path": "C:/path/to/your-service",       // ★ 脚本所在真实家（与服务注册条同语义）
  "enabled": true,                          // false = 暂停调度（面板显示已停用）
  "display": "每日签到",
  "description": "工作日早上自动签到",
  "entry": "checkin.ps1",                   // 相对 path；支持 .py/.ps1/.cmd/.bat/.exe
  "args": [],
  "cwd": ".",
  "schedule": {"type": "daily", "at": "08:30"},   // 或 {"type": "interval", "every_sec": 3600}
  "timeout_sec": 300,                       // 挂死保护（默认 300，下限 5s），超时 taskkill /T 树杀记失败
  "catch_up": false,                        // 错过是否补跑（默认 false）
  "env": {}                                 // 附加环境变量
}
```

**调度语义**（实弹验证过的边界行为）：

- **daily**：每天本地时刻 `HH:MM` 触发；**interval**：固定间隔循环，新任务从
  被调度器见到起算一个周期后首跑（宿主频繁重启不会引发齐跑）
- **防重叠**：上一轮没跑完，本轮到点不触发；跑完后 next_run 若已过期走统一的
  "错过"处理，绝不堆积连发
- **错过**（宿主关闭/睡眠/执行超长导致，宽限阈 60 秒）：`catch_up: true` 立即
  补跑一次（签到类建议开）；默认 `false` 只登记 `cron_skip` 并顺延到下一合法
  时刻，开机绝不齐跑
- **状态恢复**：`last_run/last_ok/last_rc/next_run` 持久化在
  `logs\cron_state.json`（scheduler 独写，运行期以内存为准），宿主重启后照单恢复
- **手动触发**：面板「▶ 立即运行」按钮 / `svc.ps1 cron-run <名>` /
  `POST /api/crons/{名}/run`；运行中重复触发返回 409 拒绝

**日志边界（与服务同一条铁律）**：脚本输出一律 DEVNULL 不接管——任务日志由
任务自己写（一次性脚本建议自行追加写自己的日志文件）；框架只把"何时触发了、
成没成、耗时、退出码"记进登记簿（`cron_run`/`cron_skip`）。

**注册/注销**：在 `reg\cron\` 放/删 `<任务名>.json` 即可，无需重启宿主；
无效注册条在面板标 invalid 并附原因（缺 schedule、入口不存在等）。

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
（`timeout_sec` 内未达健康判定）。这四类原因都会出现在登记簿的 `note`
（卡片「事件」或 `svc.ps1 events <name>`）；至于服务**内部**为什么起不来，
看服务自己家 `logs\` 的日志——框架不代管，也不代理读取。

failed 提示「检测到已有实例在运行」= **正常防护**，说明服务其实活着（外部/残留实例），
按需去对方那边停止，或直接点面板重启走框架原子切换；提示「健康目标已被占用」同理。

### 完整实例：接入一个自带官方启停脚本的商业软件（some-service）

真实文件 `reg\some-service.json`（detached 型、零副本单处维护）：

```json
{
  "path": "C:/path/to/your-service",
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

要点回顾：`path`=服务在 `C:\path\to\` 下的家目录（**exe 与数据分离的服务，cwd 钉死在此防漂移空库**）、
entry/stop_script 直指官方脚本（`adapters\` 留空=不劫持）、`env` 关掉官方脚本交互等待。

## 用户操作接口

| 层 | 入口 |
|:--|:--|
| Web Dashboard | http://127.0.0.1:1236 （一行一服务：状态灯/启停/↗跳转（未运行自动拉起）/**事件窗**/自启开关；下方「⏱ 定时任务」区块：调度规则/下次执行/上次结果/▶ 立即运行；header 右上含宿主运行时长 + `▤ 登记簿` + 重启/停止按钮） |
| 用户操作入口 | `entry\start.ps1`、`entry\stop.ps1`、`entry\restart.ps1`（PowerShell 运行；幂等）、`entry\Dashboard.url`（双击开面板） |
| 服务 CLI | `.\src\svc.ps1 ls\|crons\|cron-run <名>\|events [name] [n]\|host-err [n]\|start\|stop\|restart <name>`（终端/AI 会话用；`events` 读的是启停登记簿，服务业务日志请去服务家 `logs\`） |
| REST API | `GET /api/services`、`GET /api/host`(宿主 pid/uptime)、`GET /api/audit?n=&svc=&fail=1`(启停登记簿)、`GET /api/host-err?n=`(宿主异常栈)、`GET /api/crons`(定时任务列表)、`POST /api/services/{svc}/start\|stop\|restart[?via=cli]`、`POST /api/crons/{name}/run[?via=cli]`(手动触发)、`POST /api/config/{svc}`、`POST /api/host/stop\|restart` |
| 注册服务 | 在 `my-service\reg\` 放 `<服务名>.json`；注册定时任务在 `my-service\reg\cron\` 放 `<任务名>.json`；注销 = 删除注册条或 `"enabled": false` |

## 快速上手

```powershell
cd C:\path\to\my-service
# 图形入口：直接打开 entry\ 文件夹，运行 start.ps1，或双击 Dashboard.url
pwsh -File .\entry\start.ps1             # 命令行起宿主
start http://127.0.0.1:1236              # 打开面板
.\entry\install_autostart.ps1          # 安装自启（默认 Startup 模式：启动文件夹；可 -Mode Logon/Boot 换计划任务方案）
```
## 排坑记录

### 按需服务启动被「无监听端口探测」拖慢数秒（已解决 2026-09-03）

**现象**：on_demand 服务点开冷启动 6.2 秒，其中约 4 秒花在「探测一个还没起来的服务端口」。

**根因**：本机由 clash 类 TUN / 安全软件的流量过滤，把「连一个无监听的端口」这种
本应瞬时返回"拒绝"的操作，拖成**每地址 2 秒**才超时；`localhost` 解析成 IPv6+IPv4
两个地址 → 一次防双开探测就 4 秒。纯 `socket` 直连同样慢，属系统层，非代码问题。

**解决**：
1. 防双开（`external_alive`）与 starting 判定改用 `runner.port_listening()` 快速端口探测
   （50ms 超时），不再用 urlopen 做可达判断——真服务 12ms 内必答，空门 50ms 即放弃
2. 健康 `url` 的 host 按服务实际监听栈选（见注册协议节），避免探测先敲一个服务没监听的地址
3. 健康探测统一走 `runner._NO_PROXY_OPENER`（`ProxyHandler({})`），强制绕过代理直连本机（防御性加固）

**效果**：冷启动 6245ms → 646ms（探测仅剩 ~64ms，其余是进程冷启动的固有开销）。

### 登录自启黑窗（已解决 2026-08-30）

**现象**：登录触发自启时闪一个无内容黑窗，内含一行 `pid=xxxx`（实为 `ctl.ps1` 的 `Write-Host` 输出在任务外层 pwsh 的控制台里）。

**根因**：计划任务以「只在用户登录时运行」（InteractiveToken）运行——pwsh 作为控制台程序
必须借用户桌面开一个 conhost 窗口短暂显示，哪怕只活 1-2 秒。

**试错路径（勿重蹈）**：
1. `pwsh -WindowStyle Hidden` ——实测无效，窗口仍闪
2. wscript + vbs 包装（SW_HIDE）——能根治，但引入新文件类型、链路变复杂，已弃用并撤销

**最终方案——S4U 后台任务**：把任务主体改成「不管用户是否登录都要运行」的 **S4U** 登录类型
（后台房间：逻辑上无窗可弹，物理根治）。改动仅在 `entry\install_autostart.ps1` Logon 分支：

```powershell
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType S4U
Register-ScheduledTask ... -Principal $principal ...
```

要点：
- 触发器仍 `AtLogOn`：登录才触发，时机不变；只把运行场所从"交互桌面"换到"后台房间"
- S4U 不存密码 → 无网络凭据（SMB/域资源不可达），本机场景零影响；RunLevel 必须 Limited
- 手动运行 `entry\start.ps1` / 面板 / `svc.ps1` 不经任务计划程序，行为一字不变
- 全链窗口语义已审计：python 子进程 `-WindowStyle Hidden`、托管服务与 stop 脚本 `CREATE_NO_WINDOW`、
  宿主重建 `DETACHED_PROCESS`——除任务外层之外全部本就无窗

**实践演进（2026-09-05）**：S4U 方案实际用下来，任务进程跑在「后台房间」
（非交互窗口站），对习惯于「进程就在我桌面上、随手可脚本操作」的场景仍显别扭；
当日改成 **启动文件夹方案** 并沿用至今（git 无记录——改动在系统目录，不在仓库）：

```
%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\start-my-service.cmd   （GBK）
@echo off
start "" /min pwsh -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "C:\path\to\my-service\entry\start.ps1" start
```

- 登录时由启动体验执行，进程跑在**当前用户交互会话** → `svc.ps1`/`ctl.ps1`/面板直接可操作，无凭据限制、无 session 0 概念
- 黑窗：`start "" /min` + `-WindowStyle Hidden` 最小化秒退，实测可接受
- 卸载 = 删除该 cmd（`uninstall_autostart.ps1` 一并清理计划任务，双保险）
- 计划任务方案（S4U / SYSTEM-AtStartup / nssm 服务）保留为 `install_autostart.ps1` 的 `-Mode` 备选