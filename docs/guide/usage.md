# 使用指南

> 状态：Accepted ｜ 面向：日常操作、终端 / AI 会话、脚本调用

## 四层入口

| 层 | 入口 |
|:--|:--|
| Web Dashboard | `http://127.0.0.1:1236` |
| 人的入口 | `run/start.ps1`、`run/stop.ps1`、`run/restart.ps1`（幂等）、`run/Dashboard.url`（首次启动时按本机 config 生成，双击开面板；不入库） |
| 服务 CLI | `./src/svc.ps1 ls \| crons \| cron-run <名> \| events [name] [n] \| host-err [n] \| start \| stop \| restart <name>` |
| REST API | 见下表 |

> `svc.ps1 events` 读的是启停登记簿；服务业务日志请去服务家 `logs/`。

## REST API

| 方法 | 路径 | 说明 |
|:--|:--|:--|
| GET | `/health` | 宿主存活 |
| GET | `/api/host` | 宿主 pid / uptime / version |
| GET | `/api/services` | 服务列表（含状态、端口、关联任务） |
| GET | `/api/services/{name}` | 单服务 |
| GET | `/api/audit?n=&svc=&fail=1` | 启停登记簿 |
| GET | `/api/host-err?n=` | 宿主异常栈 |
| GET | `/api/crons` | 定时任务列表 |
| POST | `/api/services/order` | `{"names":[...]}` 拖拽排序 |
| POST | `/api/services/{svc}/start\|stop\|restart[?via=cli]` | 启停 |
| POST | `/api/services/{svc}/config` | `{"idle_timeout_sec": N}`（仅 on_demand） |
| POST | `/api/crons/order` | `{"names":[...]}` 拖拽排序 |
| POST | `/api/crons/{name}/run[?via=cli]` | 手动触发 |
| POST | `/api/crons/{name}/config` | `enabled` / `catch_up` / `timeout_sec` / `schedule` 任意组合 |
| POST | `/api/config/{svc}` | `{"autostart": bool}` |
| POST | `/api/audit/clear` | `{"svc":"名"}` 清该名事件 / `{}` 清全部 |
| POST | `/api/host/stop\|restart` | 宿主自身生命周期 |

## 快速上手

```powershell
cd C:\path\to\my-service
# 图形入口：直接打开 run/ 文件夹，运行 start.ps1，或双击 Dashboard.url（首次 start 时自动生成）
pwsh -File .\run\start.ps1               # 命令行起宿主
start http://127.0.0.1:1236              # 打开面板
.\run\install_autostart.ps1              # 安装自启（默认 Startup 启动文件夹方案）
```

`install_autostart.ps1` 可用 `-Mode Logon/Boot` 切换到计划任务方案；卸载用 `uninstall_autostart.ps1`（同时清理计划任务，双保险）。

## 注意（AI / 自动化会话）

agent 启动的宿主会随会话结束消失（Windows Job Object 连带清理）。正确做法：

- 第一代宿主由用户双击 `run/start.ps1` 或开机自启拉起；
- agent 改完代码后用 `POST /api/host/restart`（等价面板 `⟳ 重启`）重载，新进程由旧宿主派生，能跨会话存活；
- 禁止 agent 用 `python src/server.py` 后台直跑方式长期托管宿主。

完整背景见 [analysis/pitfalls.md](../analysis/pitfalls.md#ai自动化会话改代码后如何重载宿主实测-2026-09-08)。
