# 注册条协议（reg/services/*.json）

> 状态：**已实现（后端）**（2026-11-14 重构，明细见 [CHANGELOG](../../CHANGELOG.md) 的 v0.13.0 条目）
> 平台：当前仅 Windows（端口→PID、进程创建时间依赖 Win32 API）
> 实现：`src/core/scanner.py`

## 原则

- **一个条目 = 一个对象**，不搞字符串/对象双形态；默认值不写，只写非默认项。
- **按生命周期分组**：`start` 起、`stop` 停、`identity` 活着且是不是它、`settings` 管不管它、`schedule` 何时跑。
- **框架不读子服务源码**：端口、进程、路径全部由注册条声明。
- **谁拉起的与模型无关**：框架拉起、外部拉起、系统自启，统一按同一状态机处理。

## 三种 mode

| mode | 含义 | 必需 | 回收 |
|:--|:--|:--|:--|
| `managed` | 常驻 | `start` | **不自动回收** |
| `on_demand` | 点开才起、超时回收 | `start` | 默认 `idle_timeout_sec=600` |
| `task` | 到点跑一次 | `start`、`schedule` | 不适用 |

## 条目的六组

```json
{
  "path": "C:/path/to/your-service",
  "display": "your-service",          // 缺省 = 服务名
  "description": "示例：积分仪表盘",
  "mode": "on_demand",
  "url": "http://127.0.0.1:33510/",      // 仅前端页面地址，与探测无关

  "start":    { "entry": "src/server.py" },
  "stop":     { "script": "run/stop.ps1" },
  "identity": { "http": "http://127.0.0.1:33510/health",
                "service_name": "your-service",
                "pidfile": "data/server.pid" },

  "settings": { "state": "active", "autostart": false, "idle_timeout_sec": 600 }
}
```

### start（怎么起）

| 字段 | 默认 | 说明 |
|:--|:--|:--|
| `entry` | 必填 | 相对 `path`；`.py` → python，`.ps1` → pwsh，`.cmd/.bat` → cmd，`.exe` 直接执行 |
| `args` | `[]` | 附加参数 |
| `cwd` | `.` | 工作目录（相对 `path`） |
| `env` | `{}` | 附加环境变量 |
| `detach` | `false` | `true` = 点火脚本，拉起真进程后自己退场算正常（替代旧 `detached` 模式） |

`detach: true` 仅允许 `mode: managed`（按需服务需要句柄来回收，任务没有常驻进程）。

### stop（怎么停）

| 字段 | 默认 | 说明 |
|:--|:--|:--|
| `script` | 无 | 可选；省略 = 直接 `taskkill /T /F` |
| `grace_sec` | 10 | 停止脚本宽限 |

停止前必须确认实例身份；确认不了就拒绝停止，避免误杀。

### identity（活着 + 是不是它）

原 `health` 已并入 `identity`：写几个探测成员，就同时得到健康判据与身份凭证。至少写一个。

| 成员 | 探测 / 判据 | 必填 |
|:--|:--|:--|
| `http` | GET 该 URL，状态码 < 400 | — |
| `service_name` | 期望响应体 `service` 字段值；不符判 `stopped` | — |
| `port` | TCP 可连（同时用于端口属主核对） | — |
| `pidfile` | 读 pid + 进程存活 | — |
| `process` | 进程名或完整路径；含 `/` 或 `\` 按路径匹配，否则按名字 | — |
| `timeout_sec` | 健康等待上限，默认 `30` | — |

**健康判定优先级**（多成员共存只取最靠前的一个，失败不回退）：

```
http → port → pidfile → process → 框架句柄
```

`process` 型内部的定位顺序：

```
identity.pidfile（读 pid + 进程存活）
  → identity.process（进程名/路径唯一匹配）
    → 框架句柄（仅框架拉起的实例）
```

### identity 的身份判定（是不是它）

与健康分开，按"证据产生时机"从强到弱：

```
端口属主 → own-pidfile（框架写的 pid）→ 自报 service
  → declared-pidfile（注册条声明）→ identity.process 唯一匹配
```

确认不了身份时拒绝停止，避免误杀。

### settings（管不管它）

| 字段 | 默认 | 适用 | 说明 |
|:--|:--|:--|:--|
| `state` | `active`（task 为 `disabled`） | 全部 | `active` / `disabled` / `archived` |
| `autostart` | `false` | 全部 | 宿主启动时自动拉起 |
| `order` | 自动 | 全部 | 全局序号；面板按 `(mode, order)` 排序 |
| `idle_timeout_sec` | 600 | on_demand | 超时回收阈值；`0` = 永不 |
| `run_timeout_sec` | 300 | task | 单次执行超时，到点树杀 |
| `catch_up` | `false` | task | 错过是否补跑 |

### schedule（仅 task）

```json
"schedule": { "type": "daily", "at": ["09:30"] }
"schedule": { "type": "interval", "every_sec": 3600 }
```

- `daily.at`：每天固定时刻，可多个；缺省/空 = `09:00`；
- `weekly.days`：1=周一 … 7=周日；**空 = 不调度**（条目仍 valid）；
- `interval.every_sec`：**最小 10 秒**（与调度器检查间隔一致）；
- 缺省整组 `schedule` → 按 `daily 09:00` 计划；
- `settings.run_timeout_sec`：单次执行超时，默认 300 秒。

## 回收规则（仅 on_demand）

```
有 last_active（服务自报）→ now - last_active > idle_timeout_sec → 回收
没有                       → now - ttl_start    > idle_timeout_sec → 回收
```

`ttl_start`：

```
max(进程创建时间, 接管时刻) + 首次接入宽限（一个完整周期）
```

- 首次纳入管理时给一个完整周期，避免“刚接管就回收”；
- 进程创建时间取不到时用接管时刻；
- `managed` 永不自动回收。

## 状态与冲突

状态只有六个：`stopped / starting / running / stopping / failed / invalid`。

没有独立的 `conflict` 状态。以下情况统一为 `stopped` + `error`：

| 情况 | status | error |
|:--|:--|:--|
| 端口被别的程序占用 | `stopped` | `端口 33510 被 chrome.exe 占用（不是本服务）` |
| `/health` 自报 service 不符 | `stopped` | `健康目标自报 service=xxx，非本服务` |
| 注册条无效 | `invalid` | 具体原因 |

面板用“状态 + 错误文本”展示，不额外定义状态。

## 五种典型条目

**① Web 服务（首选）**

```json
{
  "path": "C:/path/to/your-service",
  "mode": "on_demand",
  "start":    { "entry": "src/server.py" },
  "stop":     { "script": "run/stop.ps1" },
  "identity": { "http": "http://127.0.0.1:33510/health",
                "service_name": "your-service",
                "pidfile": "data/server.pid" },
  "settings": { "state": "active", "autostart": false, "idle_timeout_sec": 600 }
}
```

**② 只有端口**

```json
{
  "path": "C:/path/to/your-service",
  "mode": "managed",
  "start":  { "entry": "bin/server.exe" },
  "stop":   { "script": "run/stop.ps1" },
  "identity": { "port": 33510, "process": "server.exe" }
}
```

**③ 无端口守护**

```json
{
  "path": "C:/path/to/your-service",
  "mode": "managed",
  "start":    { "entry": "agent.exe" },
  "stop":     { "script": "run/stop.ps1" },
  "identity": { "pidfile": "agent.pid" }
}
```

**④ 商业软件（点火脚本）**

```json
{
  "path": "C:/path/to/your-service",
  "mode": "managed",
  "url": "http://localhost:8090/",
  "start":  { "entry": "start.ps1", "detach": true, "env": { "SOME-SERVICE_NO_WAIT": "1" } },
  "stop":   { "script": "stop.ps1" },
  "identity": { "http": "http://localhost:8090/health", "timeout_sec": 60,
                "pidfile": "C:/Users/<user>/AppData/Local/<vendor>/<app>.pid" }
}
```

**⑤ 定时任务**

```json
{
  "path": "C:/path/to/your-service",
  "mode": "task",
  "start":    { "entry": "src/run_all.py", "args": ["--site", "example"] },
  "schedule": { "type": "daily", "at": ["09:30"] },
  "settings": { "state": "active", "run_timeout_sec": 300, "catch_up": false }
}
```

## 校验规则

| 规则 | 违反后果 |
|:--|:--|
| `path` 存在 | invalid |
| `start.entry` 存在且文件存在 | invalid |
| `stop.script` 存在 | invalid |
| `mode` ∈ managed / on_demand / task | invalid |
| `identity` 至少一个探测成员（http/port/pidfile/process） | invalid |
| `identity.port` 需为整数 | invalid |
| `mode=task` / `on_demand` 不允许 `start.detach: true` | invalid |
| `schedule.interval.every_sec` ≥ 10 | invalid |
| `settings.state` ∈ active / disabled / archived | invalid |
| `settings.idle_timeout_sec` ∈ 0 或 60–86400 | invalid |
| `settings.state=archived` 的条目不参与编排 | 跳过 |

## 旧字段迁移

| 旧 | 新 |
|:--|:--|
| `entry` / `args` / `cwd` / `env` | `start.entry` / `start.args` / `start.cwd` / `start.env` |
| `stop_script` | `stop.script` |
| 顶层 `pidfile` | `identity.pidfile` |
| `mode: detached` | `mode: managed` + `start.detach: true` |
| `health` 分组（`type` / `url` / `port` / `service` / `timeout_sec`） | 并入 `identity`：`http` / `port` / `service_name` / `timeout_sec`；`type` 由成员自动推导 |
| `health.type: none` | 不写任何探测成员 → 只看框架句柄 |
| `identity.match` | 删除（`process` 含 `/` 或 `\` 即按路径匹配） |
| `runtime` 分组 | `settings` |
| `runtime.enabled` | `settings.state`（true→active、false→disabled） |
| `runtime.archived` | `settings.state: archived` |
| `runtime.timeout_sec`（旧，回收） | `settings.idle_timeout_sec` |
| `reg/cron/*.json` | `reg/tasks/*.json` + `mode: task` |
| `data/service_runtime.json` / `cron_runtime.json` / `*_order.json` | 废弃：框架观测落 `data/runtime/`，用户设置落注册条 `settings` |

目录迁移：启动时若发现 `reg/*.json` 或 `reg/cron/`，自动搬到 `reg/services/` / `reg/tasks/`，并记一条 `legacy_reg` 事件。
