# 按需服务（on_demand）

> 状态：**已实现（后端）**（2026-11-14 重构）｜ 平台：当前仅 Windows

## 为什么

看一眼就走的服务（查询仪表盘）不应整天占着进程。`on_demand` 提供“点开即启、超时回收”。

**只有 `on_demand` 自动回收；`managed` 永不自动回收。**

## 点开即启

- 面板点 ↗（或整行）→ 先打开启动中转页（`web/opening.html`），不白屏；
- 中转页调 `POST /api/services/{名}/start`，再轮询目标 `/health`；
- 就绪后跳进服务页面；已在运行则几乎零等待。

## 超时回收

```
有 last_active（服务自报）→ now - last_active > idle_timeout_sec → 回收
没有                       → now - ttl_start    > idle_timeout_sec → 回收
```

`ttl_start`：

```
max(进程创建时间, 接管时刻) + 首次接入宽限（一个完整周期）
```

- 外部拉起的服务已跑很久，接入后不会立刻被回收；
- 进程创建时间取不到时用接管时刻；
- 健康探测**不算活动**，否则永远不会超时。

## 置信度

| 置信度 | 含义 | 回收 |
|:--|:--|:--|
| `confirmed` | 端口→PID→路径 / 自报 service / 框架句柄 | ✅ |
| `probable` | pidfile、进程名匹配 | ✅（pid 可定位时） |
| `unknown` | 无任何证据 | ❌（无法确认对象，不误停） |

## 面板可调

服务行 `⚙ 设置` 里改 `runtime.idle_timeout_sec`（60 秒 ~ 24 小时；`0` = 永不回收）。

- 越界值显式拒绝，不静默 clamp；
- 写回注册条前校验，不合法则回滚；
- 面板显示“≈ 距回收还剩多久”。

## 约束

- `on_demand` 必须能停止：`stop.script` 或可被 `taskkill /T` 终结；
- `health` 必须可判定（`http` / `port` / `process`）；
- 无法定位 PID（`pid = null`）时不回收，面板提示“证据不足”；
- 不允许 `start.detach: true`（点火脚本退场后没有句柄，无法可靠回收）。

## 实现

- 回收判定：`src/core/state/monitor.py`
- 定位与停止：`src/core/state/manager.py` + `src/core/runner.py`
- 注册条字段：见 [registry.md](registry.md)
