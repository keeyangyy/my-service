# 注册一个服务

> 状态：**已实现（后端）**（2026-11-14 重构）｜ 平台：当前仅 Windows

## 最小要求

| 能力 | 要求 | 不满足 |
|:--|:--|:--|
| 启动 | `start.entry` 能在命令行拉起进程 | 注册条 invalid |
| 停止 | `stop.script`，或可被 `taskkill /T` 终结 | 仍可接入，但停止能力受限 |
| 健康 | `health.type` 可判定（http / port / process） | 无法判断运行状态 |

## 三种 mode

```json
// 常驻（永不自动回收）
{ "path": "C:/path/to/xxx", "mode": "managed",
  "start": { "entry": "src/server.py" },
  "health": { "type": "http", "url": "http://127.0.0.1:33510/health" } }
```

```json
// 按需（点开才起、超时回收）
{ "path": "C:/path/to/xxx", "mode": "on_demand",
  "start": { "entry": "src/server.py" },
  "stop": { "script": "run/stop.ps1" },
  "health": { "type": "http", "url": "http://127.0.0.1:33510/health" },
  "runtime": { "idle_timeout_sec": 600 } }
```

```json
// 定时任务（间隔最小 10 秒）
{ "path": "C:/path/to/xxx", "mode": "task",
  "start": { "entry": "src/run_all.py", "args": ["--site", "example"] },
  "schedule": { "type": "daily", "at": ["09:30"] },
  "runtime": { "run_timeout_sec": 300 } }
```

## 步骤

1. 服务在家补齐启动、停止、健康（或至少启动 + 健康）。
2. 在 `my-service/reg/services/` 新建 `<服务名>.json`；定时任务放 `reg/tasks/`。
3. 保存即生效，无需重启宿主：刷新面板或 `svc.ps1 ls` 即可看到。

## 接入检查单

列表出现但状态不对，按序查（原因都写进登记簿 `note` / 面板 `error`）：

1. 注册条无效（红标 `invalid` 附原因）；
2. 端口被别的程序占用（显示 `stopped` + 具体占用者）；
3. 入口文件不存在；
4. 健康超时（`health.timeout_sec` 内未通过）。

服务**内部**为什么起不来，看服务自己 `logs/`——框架不代管。

## 常见提示

| 提示 | 含义 | 处理 |
|:--|:--|:--|
| 「检测到已有实例在运行」 | 外部/残留实例存在，框架拒绝重复拉起 | 正常防护；需要时先停外部实例 |
| 「端口 xxx 被 yyy 占用」 | 端口上是别的程序 | 改端口或停掉占用者 |
| 「身份不确认」 | 有应答但无法确认是本服务 | 补 `identity`（pidfile / 进程名）或 `/health` 的 `service` |
| 「证据不足」 | 拿不到 PID | 补 pidfile / 端口 / 进程名；此时不能停止与回收 |

## 注销与归档

| 意图 | 做法 |
|:--|:--|
| 暂时停用 | `runtime.enabled: false`（面板开关） |
| 归档留档 | `runtime.archived: true`（扫描跳过，面板折叠） |
| 彻底不要 | 删除注册条文件 |

不再使用 `reg/_archive/` 目录。
