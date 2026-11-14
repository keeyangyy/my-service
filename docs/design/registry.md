# 注册条协议（reg/services/*.json）

> 状态：**已实现（后端）**（2026-11-14 重构，见 [plan](../../plan/20261114-registry-refactor.md)）
> 平台：当前仅 Windows（端口→PID、进程创建时间依赖 Win32 API）
> 实现：`src/core/scanner.py`

## 原则

- **一个条目 = 一个对象**，不搞字符串/对象双形态；默认值不写，只写非默认项。
- **按生命周期分组**：`start` 起、`stop` 停、`health` 活没活、`identity` 是不是它、`runtime` 管不管它、`schedule` 何时跑。
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
  "url": "http://127.0.0.1:33510/",      // 缺省从 health.url 推导

  "start":    { "entry": "src/server.py" },
  "stop":     { "script": "run/stop.ps1" },
  "health":   { "type": "http", "url": "http://127.0.0.1:33510/health" },
  "identity": { "pidfile": "data/server.pid" },

  "runtime":  { "enabled": true, "autostart": false, "idle_timeout_sec": 600 }
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

### health（活没活）

| `type` | 判据 | 必填 |
|:--|:--|:--|
| `http` | GET `url`，状态码 < 400；配置 `service` 时校验响应体 | `url` |
| `port` | TCP 可连 | `port` |
| `process` | 按 `identity` 的 pidfile / 进程名 / 框架句柄判断 | 依赖 `identity` |

`process` 的判定顺序：

```
identity.pidfile（读 pid + 进程存活）
  → identity.process（进程名/路径匹配）
    → 框架句柄（仅框架拉起的实例）
```

可选字段：

| 字段 | 默认 | 说明 |
|:--|:--|:--|
| `timeout_sec` | 30 | 健康等待上限 |
| `service` | 无 | 期望的自报身份；不符 → 状态 `stopped` + error 说明 |

### identity（是不是它）

| 字段 | 说明 |
|:--|:--|
| `pidfile` | 服务或官方脚本写的 PID 文件（相对 `path` 或绝对） |
| `process` | 进程名或完整路径 |
| `match` | `name`（默认）/ `path` |

判定顺序（强 → 弱）：

```
端口 → OwningPid → 进程路径
pidfile + 进程存活 + 端口在监听
进程名/路径匹配
框架句柄（仅框架拉起的实例）
```

### runtime（管不管它）

| 字段 | 默认 | 适用 | 说明 |
|:--|:--|:--|:--|
| `enabled` | `true` | 全部 | `false` = 停用，不参与编排，也不自启 |
| `autostart` | `false` | 全部 | 宿主启动时自动拉起 |
| `archived` | `false` | 全部 | 归档：扫描跳过，面板折叠展示 |
| `order` | 自动 | 全部 | 全局序号；面板按 `(mode, order)` 排序 |
| `idle_timeout_sec` | 600 | on_demand | 超时回收阈值；`0` = 永不 |
| `run_timeout_sec` | 300 | task | 单次执行超时，到点树杀 |

### schedule（仅 task）

```json
"schedule": { "type": "daily", "at": ["09:30"] }
"schedule": { "type": "interval", "every_sec": 3600 }
```

- `daily.at`：每天固定时刻，可多个；
- `interval.every_sec`：**最小 10 秒**（与调度器检查间隔一致）；
- `runtime.run_timeout_sec`：单次执行超时，默认 300 秒。

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
  "health":   { "type": "http", "url": "http://127.0.0.1:33510/health",
                "service": "your-service" },
  "identity": { "pidfile": "data/server.pid" },
  "runtime":  { "enabled": true, "autostart": false, "idle_timeout_sec": 600 }
}
```

**② 只有端口**

```json
{
  "path": "C:/path/to/xxx",
  "mode": "managed",
  "start":  { "entry": "bin/server.exe" },
  "health": { "type": "port", "port": 33510 },
  "identity": { "process": "server.exe", "match": "name" }
}
```

**③ 无端口守护**

```json
{
  "path": "C:/path/to/xxx",
  "mode": "managed",
  "start":    { "entry": "agent.exe" },
  "health":   { "type": "process" },
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
  "health": { "type": "http", "url": "http://localhost:8090/health", "timeout_sec": 60 },
  "identity": { "pidfile": "C:/Users/<user>/AppData/Local/SomeService/some-service.pid" }
}
```

**⑤ 定时任务**

```json
{
  "path": "C:/path/to/your-service",
  "mode": "task",
  "start":    { "entry": "src/run_all.py", "args": ["--site", "example"] },
  "schedule": { "type": "daily", "at": ["09:30"] },
  "runtime":  { "enabled": true, "catch_up": false, "run_timeout_sec": 300 }
}
```

## 校验规则

| 规则 | 违反后果 |
|:--|:--|
| `path` 存在 | invalid |
| `start.entry` 存在且文件存在 | invalid |
| `mode` ∈ managed / on_demand / task | invalid |
| `mode=task` 必须有 `schedule` | invalid |
| `mode=task` / `on_demand` 不允许 `start.detach: true` | invalid |
| `health.type=http` 必须有 `url` | invalid |
| `health.type=port` 必须有 `port` | invalid |
| `health.type=process` 必须有 `identity.pidfile` 或 `identity.process` | invalid |
| `on_demand` 必须有 `stop` 或可被 `taskkill` 终结 | invalid |
| `schedule.interval.every_sec` ≥ 10 | invalid |
| `runtime.idle_timeout_sec` ∈ 0 或 60–86400 | invalid |
| `runtime.archived=true` 的条目不参与编排 | 跳过 |

## 旧字段迁移

| 旧 | 新 |
|:--|:--|
| `entry` / `args` / `cwd` / `env` | `start.entry` / `start.args` / `start.cwd` / `start.env` |
| `stop_script` | `stop.script` |
| 顶层 `pidfile` | `identity.pidfile` |
| `mode: detached` | `mode: managed` + `start.detach: true` |
| `health.type: none` | `health.type: process`（无 identity 时等同只看句柄） |
| `health.expect.service` | `health.service` |
| `runtime.timeout_sec`（旧，回收） | `runtime.idle_timeout_sec` |
| `reg/cron/*.json` | `reg/tasks/*.json` + `mode: task` |
| `reg/_archive/*.json` | 条目里 `runtime.archived: true` |
| `data/service_runtime.json` | 各条目 `runtime` |
| `data/cron_runtime.json` | 各任务条目 `runtime` |
| `data/service_order.json` / `cron_order.json` | 各条目 `runtime.order` |

目录迁移：启动时若发现 `reg/*.json` 或 `reg/cron/`，自动搬到 `reg/services/` / `reg/tasks/`，并记一条 `legacy_reg` 事件。
