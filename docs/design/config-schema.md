# 配置与注册条规范（定稿）

> 状态：**Accepted**（2026-11-16 定稿）｜ 实现：`src/core/scanner.py`、`state/manager.py`、`runner.py`、`scheduler.py`
> 替代：[registry.md](registry.md) 中 health/identity/runtime 的旧描述

## 原则

1. **启停接口不削减**：`start` / `stop` 必须显式写，保留完全灵活性。
2. **按来源分字段**：用户声明（可配置）在 `reg/`；框架观测（不可配置）在 `data/runtime/`。
3. **默认值不写**：省略即默认；`check` 命令展示生效值与来源。
4. **框架不改写用户文件**：面板写回只改目标键，保留键顺序与其他字段。

## 注册条结构

```json
{
  "path": "C:/path/to/your-service",
  "display": "面板显示名",
  "description": "说明",
  "mode": "managed",
  "url": "http://localhost:33510/",

  "start": { "entry": "run/start.ps1" },
  "stop":  { "script": "run/stop.ps1" },

  "identity": {
    "http": "http://127.0.0.1:33510/health",
    "service_name": "your-service",
    "port": 33510,
    "pidfile": "logs/server.pid",
    "process": "C:/path/to/python.exe",
    "timeout_sec": 30
  },

  "settings": {
    "state": "active",
    "autostart": false,
    "order": 1,
    "idle_timeout_sec": 600,
    "run_timeout_sec": 300,
    "catch_up": false
  },

  "schedule": { "type": "daily", "at": ["09:00"] }
}
```

### 顶层

| 字段 | 必写 | 默认 | 说明 |
|:--|:--|:--|:--|
| `path` | ✅ | — | 服务家目录 |
| `display` | — | 目录名 | 面板显示名 |
| `description` | — | 空 | 面板描述 |
| `mode` | ✅ | — | `managed` / `on_demand` / `task` |
| `url` | — | 无 | **前端页面地址**，仅用于面板跳转；没有表示该服务无页面 |

### start / stop

| 组 | 字段 | 必写 | 默认 |
|:--|:--|:--|:--|
| `start` | `entry` | ✅ | — |
| | `args` / `cwd` / `env` / `detach` | — | `[]` / `.` / `{}` / `false` |
| `stop` | `script` | ✅ | — |
| | `grace_sec` | — | `10` |

### identity（身份 + 健康）

原 `health` 与 `identity` 合并。至少写一个探测成员。

| 字段 | 类型 | 说明 |
|:--|:--|:--|
| `http` | URL | HTTP 健康探测地址 |
| `service_name` | 字符串 | 期望响应体 `service` 字段值；不符判 `stopped` |
| `port` | 数字 | 端口（探测 + 身份属主） |
| `pidfile` | 路径 | PID 文件（探测 + 身份） |
| `process` | 字符串 | 进程名或完整路径（探测 + 身份） |
| `timeout_sec` | 数字 | HTTP 探测超时，默认 `30` |

**健康判定优先级**（identity 可同时声明多个成员，只取优先级最高的一个，失败不回退）：

```
http → port → pidfile → process → 框架句柄
```

多成员共存时（例如同时写 `http` 和 `port`），健康只看最靠前的方法——`http` 挂了即使端口仍在监听也判不健康；其余成员仍参与身份判定（`identify`）。

**身份判定顺序**：

```
端口属主 → pidfile → process → 框架句柄
```

`process` 的匹配方式自动推断：**含 `/` 或 `\` 按完整路径匹配，否则按进程名匹配**；不再有 `match` 字段。按名字匹配到多个进程时，框架拒绝停止（防误杀）。

`service_name` 只校验响应体里的 `service` 字段，防止端口被别的服务占用时误判健康。

### settings（原 runtime）

| 字段 | 默认 | 适用 | 说明 |
|:--|:--|:--|:--|
| `state` | `active` | 全部 | `active` / `disabled` / `archived` |
| `autostart` | `false` | 全部 | 宿主启动时自动拉起 |
| `order` | 自动 | 全部 | 面板排序；可手写，配置文件为准，拖拽会写回 |
| `idle_timeout_sec` | `600` | on_demand | 空闲回收；`0` = 永不 |
| `run_timeout_sec` | `300` | task | 单次执行超时 |
| `catch_up` | `false` | task | 错过是否补跑 |

`state` 语义：

- `active`：正常参与编排
- `disabled`：停用，不启停、不自启、不调度
- `archived`：归档，扫描跳过、面板折叠

### schedule（task）

| 字段 | 默认 | 说明 |
|:--|:--|:--|
| `type` | `daily` | `daily` / `weekly` / `interval` |
| `at` | `["09:00"]` | daily/weekly 的执行时刻 |
| `days` | `[]` | weekly 的星期（1=周一…7=周日）；**空 = 不调度** |
| `every_sec` | `3600` | interval 间隔，下限 10 秒 |

task 缺省 `schedule` 时按 daily 09:00 计划，但 `task` 的 `state` 默认 `disabled`——注册后需手动启用才调度。

## 目录形态（转接层）

```
reg/services/ollama/
├── reg.json          # 声明；path 默认为本目录
├── start.ps1
├── stop.ps1
└── README.md
```

发现顺序：`<名>/reg.json` 优先，其次 `<名>.json`。原 `adapters/` 迁到此形态。

## 配置与观测分离

| 位置 | 内容 | 谁写 |
|:--|:--|:--|
| `reg/services/<名>.json` | 全部用户声明 | 用户 / 面板 |
| `reg/tasks/<名>.json` | 全部用户声明 + schedule | 用户 / 面板 |
| `data/runtime/services/<名>.json` | status / pid / started_at / last_active | 框架 |
| `data/runtime/tasks/<名>.json` | last_run / last_ok / last_rc / next_run | 框架 |
| `data/host.pid`、`data/logs/host.jsonl` | 宿主 pid 与审计 | 框架 |

观测值可随时删；配置不受影响。

## config.json

```json
{
  "server": { "host": "127.0.0.1", "port": 1236 },
  "cors": { "local_only": true, "allow": [] },
  "audit": { "retention_days": 7 },
  "backup": { "keep": 7 },
  "update": { "mirrors": [] },
  "advanced": {
    "reg_dir": "reg",
    "monitor": { "fast_sec": 1, "idle_sec": 10, "panel_grace_sec": 15 },
    "scheduler": { "tick_sec": 10 }
  }
}
```

面板设置只暴露常用项（事件保留、备份保留、立即备份）；其余收进「高级」折叠。

## 配置检测器

```
python src/service_cli.py check          # 人读
python src/service_cli.py check --json   # 机读
```

- 只读，不修改文件、不触发启停
- 输出三类：`errors`（必须修）/ `derived`（自动推导）/ `defaults`（生效默认值）
- 全部有效退出码 0；存在 invalid 退出码 1
- 主要给 AI/CI 调用，面板不展示

## 迁移

- `runtime` → `settings`，不兼容旧名
- `enabled:false` → `state:disabled`；`archived:true` → `state:archived`
- `health` + `identity` → `identity`；`service` → `service_name`
- 删除 `identity.match`（自动推断）
- `adapters/<名>/` → `reg/services/<名>/`
- 现有注册条手工修改；`check` 用于迁移后核对