# 注册一个服务

> 面向：把本机已有服务接入 my-service ｜ 平台：仅 Windows ｜ 版本口径：0.19+

## 接入前准备（三件套）

| 能力 | 要求 | 缺了会怎样 |
|:--|:--|:--|
| 启动 | `start.entry` 能在命令行拉起进程（相对 `path`） | 注册条 `invalid` |
| 停止 | `stop.script`（必填，哪怕只是一行 `taskkill` 的脚本） | 注册条 `invalid` |
| 活着 | `identity` 里至少写一个探测成员（`http` / `port` / `pidfile` / `process`） | 注册条 `invalid` |

启动、停止脚本放进**服务自己的目录**。0.19 起框架不再有 `adapters/` 兼容层，相对路径只在 `path` 下解析。

## 三种 mode

```json
// managed：常驻，宿主起来就托管，不自动回收
{
  "path": "C:/path/to/your-service",
  "display": "your-service",
  "mode": "managed",
  "start": { "entry": "run/start.ps1", "detach": true },
  "stop": { "script": "run/stop.ps1" },
  "identity": { "http": "http://127.0.0.1:33510/health", "pidfile": "data/server.pid" },
  "settings": { "state": "active", "autostart": false }
}
```

```json
// on_demand：点开才起，空闲超时回收
{
  "path": "C:/path/to/your-service",
  "display": "your-service",
  "mode": "on_demand",
  "url": "http://127.0.0.1:33510/",
  "start": { "entry": "src/server.py" },
  "stop": { "script": "run/stop.ps1" },
  "identity": { "http": "http://127.0.0.1:33510/health" },
  "settings": { "state": "active", "idle_timeout_sec": 600 }
}
```

```json
// task：到点跑一次，无常驻进程
{
  "path": "C:/path/to/your-service",
  "display": "每日任务",
  "mode": "task",
  "start": { "entry": "src/run_all.py", "args": ["--site", "example"] },
  "schedule": { "type": "daily", "at": ["09:30"] },
  "settings": { "state": "active", "run_timeout_sec": 300, "catch_up": false }
}
```

`task` 的 `settings.state` **默认是 `disabled`**——注册后要在面板打开开关才会真正调度。

## 字段速查

| 组 | 字段 | 必写 | 默认 | 说明 |
|:--|:--|:--:|:--|:--|
| 公共 | `path` | ✅ | — | 服务真实目录（绝对路径或相对注册条） |
| | `display` / `description` | — | 服务名 / 空 | 面板显示名与说明 |
| | `mode` | ✅ | — | `managed` / `on_demand` / `task` |
| | `url` | — | 无 | 前端页面地址，仅用于面板跳转，与探测无关 |
| `start` | `entry` | ✅ | — | 相对 `path`；`.py` / `.ps1` / `.cmd` / `.bat` / `.exe` |
| | `args` / `cwd` / `env` | — | `[]` / `.` / `{}` | 附加参数、工作目录、环境变量 |
| | `detach` | — | `false` | 点火脚本：拉起真进程后自己退场算正常（仅 `managed`） |
| `stop` | `script` | ✅ | — | 停止脚本（相对 `path`） |
| | `grace_sec` | — | `10` | 停止宽限秒数 |
| `identity` | `http` | — | 无 | GET 该 URL，状态码 < 400 算活 |
| | `service_name` | — | 注册名 | 期望响应体里的 `service` 字段；不符判 `stopped` |
| | `port` | — | 无 | TCP 可连；同时用于端口属主核对 |
| | `pidfile` | — | 无 | PID 文件（相对 `path` 或绝对） |
| | `process` | — | 无 | 进程名或完整路径；含 `/` 或 `\` 按路径匹配 |
| | `timeout_sec` | — | `30` | 启动时等待健康通过的上限 |
| `settings` | `state` | — | `active`（task 为 `disabled`） | `active` / `disabled` / `archived` |
| | `autostart` | — | `false` | 宿主启动时自动拉起（`on_demand` 不支持） |
| | `idle_timeout_sec` | — | `600` | 仅 `on_demand`；`0` = 永不回收 |
| | `run_timeout_sec` | — | `300` | 仅 `task`；超时整棵进程树强杀 |
| | `catch_up` | — | `false` | 仅 `task`；错过计划是否补跑 |
| | `order` | — | 自动 | 面板排序序号（拖拽会写回） |
| `schedule` | `type` | — | `daily` | `daily` / `weekly` / `interval` |
| | `at` | — | `["09:00"]` | daily / weekly 的时刻，可多个 |
| | `days` | — | `[]` | weekly 的星期（1=周一 … 7=周日）；**空 = 不调度** |
| | `every_sec` | — | `3600` | interval 间隔，下限 10 秒 |

### identity 怎么写

写几个成员就同时得到「健康判据」和「身份凭证」，可多写。健康判定按固定优先级取**最靠前的一个**，失败不回退：

```
http → port → pidfile / process（框架句柄兜底）
```

也就是说 `http` 和 `port` 都写时，只看 `http`——`http` 挂了即使端口还在监听也判不健康。其余成员继续参与「端口上跑的是不是本服务」的身份判定，证据链：

```
框架亲手拉起的句柄 → 框架自己的 pid 文件 → 服务自报 service → 注册条声明的 pidfile → 进程名唯一匹配
```

身份确认不了时框架**拒绝停止**，避免误杀。`process` 按名字匹配到多个进程时同样拒绝。

### 什么时候用哪个成员

| 场景 | 建议写法 |
|:--|:--|
| 有 HTTP 健康接口 | `http` + `service_name`（防端口被别的服务占用） |
| 只有端口 | `port`，能加 `process` 更稳 |
| 无端口的后台进程 | `pidfile`（服务自己写 pid）或 `process` |
| 第三方软件（不敢改它的目录） | `http` 或 `port` + 自带 pidfile |

## 步骤

1. 在服务家补齐启动、停止、健康三件套（脚本写在服务目录里）。
2. 新建注册条：
   - 单文件形态：`my-service/reg/services/<服务名>.json`
   - 目录形态：`my-service/reg/services/<服务名>/reg.json`（`path` 缺省为本目录）
3. 保存即生效，**无需重启宿主**：刷新面板，或 `python src/service_cli.py ls` 查看。
4. 状态不对就按下一节逐条排查。

## 定时任务

注册条放 `reg/tasks/<任务名>.json`，`mode` 必须是 `task`：

```json
{
  "path": "C:/path/to/your-service",
  "display": "每日签到",
  "mode": "task",
  "start": { "entry": "src/checkin.py", "args": ["--site", "example"] },
  "schedule": { "type": "daily", "at": ["09:30", "14:30"] },
  "settings": { "state": "active", "run_timeout_sec": 300, "catch_up": false }
}
```

| 类型 | 写法 | 要点 |
|:--|:--|:--|
| daily | `{"type": "daily", "at": ["09:30"]}` | 可写多个时刻；空 `at` 按 `09:00` |
| weekly | `{"type": "weekly", "days": [1,3,5], "at": ["09:30"]}` | `days` 空 = 条目不报错但**不调度** |
| interval | `{"type": "interval", "every_sec": 3600}` | 最小 10 秒；首次见到起算一个周期 |

三个容易踩的点：

- `task` 的 `state` 默认 `disabled`，注册完必须手动启用；
- 错过计划（宿主关机/睡眠）时 `catch_up: true` 会补跑一次，`false` 只记一条 `cron_skip` 并顺延；
- 任务输出不接管，日志请任务自己写。

## 接入检查单

列表里出现但状态不对，按序查（原因会写进面板 `error` 与登记簿）：

1. 注册条 `invalid`：看 `error` 里的具体字段；
2. 端口被别的程序占用：显示 `stopped` + 占用者进程名；
3. 入口文件不存在：`start.entry` / `stop.script` 拼错，或脚本没放进服务目录；
4. 健康超时：`identity.timeout_sec` 内没通过，服务自身启动慢就调大；
5. 身份不确认：有应答但无法证明是本服务，补 `identity.pidfile` / `process` 或 `service_name`。

服务**内部**为什么起不来，看服务自己的 `logs/`——框架不代管服务输出。

## 常见提示

| 提示 | 含义 | 处理 |
|:--|:--|:--|
| 「检测到已有实例在运行」 | 外部/残留实例存在，拒绝重复拉起 | 正常防护；需要时先停外部实例 |
| 「端口 xxx 被 yyy 占用」 | 端口上是别的程序 | 改端口或停掉占用者 |
| 「身份不确认」 | 有应答但无法确认是本服务 | 补 `identity`（pidfile / 进程名）或 `service_name` |
| 「证据不足」 | 拿不到 PID | 补 pidfile / 端口 / 进程名；此时不能停止与回收 |
| 「旧版字段 ... 已废弃」 | 用了 0.18 之前的 `health` / `runtime` / 顶层 `entry` | 按本页字段表改写 |

## 注销与归档

| 意图 | 做法 |
|:--|:--|
| 暂时停用 | `settings.state: "disabled"`（面板开关等价） |
| 归档留档 | `settings.state: "archived"`（扫描跳过，面板折叠） |
| 彻底不要 | 删除注册条文件（`reg/` 是唯一事实源） |
