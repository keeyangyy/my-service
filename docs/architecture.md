# 架构总览

> 状态：**已实现（后端）**（2026-11-14 重构，明细见 [CHANGELOG](../CHANGELOG.md) 的 v0.13.0 条目）
> 平台：当前仅 Windows。

## 定位（三条铁律）

1. **my-service 只是框架**：`src/` 内永不出现任何服务的业务源码。
2. **注册 = 在 `reg/services/` 放一条注册**：每条指向服务真实家；没有注册条的服务与框架无关。
3. **宿主不进服务列表**：不写自己的注册条，`⟳重启 / ■停止宿主` 走 `/api/host/*` 专属通道。

## 进程与循环

```
宿主进程（127.0.0.1:1236）
├── 主线程            HTTP：面板 + REST API
├── monitor 循环      面板活跃 1s / 空闲 10s（自适应）
│     ├── 扫描 reg
│     ├── 状态收敛 + 健康探测
│     ├── on_demand 超时回收
│     └── 每小时顺带清理登记簿
└── scheduler 循环    默认 10s（可独立配置）
      └── 检查定时任务是否到点
```

- 写操作（启动/停止/改配置）后主动唤醒 monitor，立即收敛一次，不等下一轮。
- 面板心跳走 `/api/panel/ping`；其他 API（脚本调用）不计入“面板活跃”。
- 启动顺序：`host_start` 先落，再起 monitor / scheduler / autostart。

## 模块

```
src/
├── server.py          组装入口
├── service_cli.py     启停引擎 + 服务 CLI（唯一实现）
└── core/
    ├── host.py        HostController + VERSION
    ├── api/           路由 · 跨源（cors）· 静态（static）
    ├── state/
    │   ├── manager.py 状态机 + 账本 + 配置 + 排序
    │   └── monitor.py 自适应收敛 + 回收 + 清理
    ├── scheduler.py   定时任务
    ├── runner.py      进程操作 + 健康探测 + 进程信息（创建时间 / 端口→PID）
    ├── scanner.py     注册条解析与校验
    └── audit.py       登记簿
```

依赖单向、无环：

```
scanner ← runner ← state ← api ← server
                     ↑
                 scheduler
```

## 数据流（点一次启动）

```
面板 → POST /api/services/{名}/start
  → api 门禁 → Manager.start
  → 定位实例（句柄 / pidfile / 端口→PID / 进程名）
  ├─ 已在运行 → 归位 running（不重复拉起）
  └─ 未运行   → runner 拉起 → starting
  → 唤醒 monitor → 健康通过 running / 超时 failed
  → audit.emit → data/logs/host.jsonl
```

## 状态机（六态）

```
stopped ──start──▶ starting ──健康通过──▶ running
   ▲                  │                     │
   │                  │ 超时/进程退出        │ stop
   │                  ▼                     ▼
   └──── stop ──── failed ◀──崩溃──── stopping
                                             │
                                             ▼
                                          stopped

注册条无效 ─────────────▶ invalid（不参与编排）
```

| 状态 | 含义 |
|:--|:--|
| `stopped` | 未运行（端口被占、身份不符也归入此态，原因写在 `error`） |
| `starting` | 已拉起，等健康 |
| `running` | 健康通过 |
| `stopping` | 正在停 |
| `failed` | 启动失败 / 崩溃 |
| `invalid` | 注册条无效 |

没有独立的 `conflict` 状态：端口被外人占、自报 service 不符，统一是 `stopped` + `error`。

## 状态对象（9 字段）

```json
{
  "status": "running",
  "pid": 22808,
  "pid_source": "port",
  "started_at": 1791468000,
  "started_source": "process",
  "first_seen_at": 1791468100,
  "last_active": 1791468500,
  "confidence": "confirmed",
  "error": null
}
```

| 字段 | 取值 | 用途 |
|:--|:--|:--|
| `status` | 六态之一 | 状态机 |
| `pid` | 数字 / null | 停止、回收 |
| `pid_source` | handle / framework-pidfile / service-pidfile / port / process / null | 面板显示“怎么定位的” |
| `started_at` | 数字 / null | 超时起点 |
| `started_source` | process / framework / adopted / first-seen | 起点精度 |
| `first_seen_at` | 数字 | 兜底起点（不持久化，重启重算） |
| `last_active` | 数字 / null | 空闲判定 |
| `confidence` | confirmed / probable / unknown | 如实展示 |
| `error` | 字符串 / null | 失败或冲突原因 |

## 回退链

**PID**

```
框架句柄 → 框架 pids/ 记账 → 服务 pidfile → 端口 OwningPid → 进程名匹配 → null
```

**started_at**

```
进程创建时间 → 框架拉起时刻 → 接管时刻 → 首次观测时间
```

**last_active**

```
服务自报 → null（改用 started_at 计算超时）
```

**回收（仅 on_demand）**

```
ttl_start = max(进程创建时间, 接管时刻) + 首次接入宽限（一个周期）
有 last_active → now - last_active > idle_timeout_sec → 回收
没有           → now - ttl_start    > idle_timeout_sec → 回收
```

## 十种环境场景

| # | 场景 | PID 来源 | started_at | 置信度 |
|:--|:--|:--|:--|:--|
| 1 | 框架拉起 + 端口 + 自报 | 句柄 | 进程时间 | confirmed |
| 2 | 框架拉起 + 端口 | 句柄 | 进程时间 | confirmed |
| 3 | 框架拉起 + 无端口 + pidfile | 句柄 | 进程时间 | confirmed |
| 4 | 外部拉起 + 端口 + 自报 | 端口→PID | 进程时间 | confirmed |
| 5 | 外部拉起 + 端口 | 端口→PID | 进程时间 | probable |
| 6 | 外部拉起 + pidfile | pidfile | 进程时间 | probable |
| 7 | 外部拉起 + 仅进程名 | 进程名匹配 | 进程时间 | probable |
| 8 | 点火脚本 + 端口 | 端口→PID | 进程时间 | confirmed |
| 9 | 宿主重启后接管 | 框架 pidfile | 进程时间 | confirmed |
| 10 | 无任何证据 | null | 首次观测 | unknown |

第 7 种：同名多实例时停止按钮置灰。
第 10 种：不能停止/回收，其余状态正常展示；出现端口或 pidfile 后自动升级。

## 数据与记录

| 位置 | 内容 | 性质 |
|:--|:--|:--|
| `reg/services/` `reg/tasks/` | 服务与任务条目 | 人写 |
| `data/host.pid` | 宿主 PID | 瞬态 |
| `data/pids/<服务>.pid` | 框架拉起的 PID | 瞬态 |
| `data/cron_state.json` | 任务上次/下次运行 | 瞬态 |
| `data/entry-out/<服务>.out` | 仅点火脚本输出（失败诊断） | 瞬态 |
| `data/logs/host.jsonl` | 登记簿 | 记录 |
| `data/logs/host.err.log` | 宿主异常栈 | 记录 |

## 实施状态

本页描述的目标结构已于 2026-11-14 落地，变更明细见 [CHANGELOG](../CHANGELOG.md) 的 Unreleased 条目。

