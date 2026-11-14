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
.\run\install_autostart.ps1              # 安装开机自启（启动文件夹 .lnk → pythonw，无窗口）
```

自启是启动文件夹里的 `start-my-service.lnk`（目标 `pythonw.exe` + `src\server.py`），登录时不弹窗、不依赖 pwsh；面板 ⚙ 设置里的开关等价。
卸载用 `uninstall_autostart.ps1`（一并清理历史 vbs/cmd 与计划任务）。计划任务 / Nssm 等高级方式已从脚本移除，手工做法见仓库内 `docs/analysis/pitfalls.md`。

## 注意（AI / 自动化会话）

agent 启动的宿主会随会话结束消失（Windows Job Object 连带清理）。正确做法：

- 第一代宿主由用户双击 `run/start.ps1` 或开机自启拉起；
- agent 改完代码后用 `POST /api/host/restart`（等价面板 `⟳ 重启`）重载，新进程由旧宿主派生，能跨会话存活；
- 禁止 agent 用 `python src/server.py` 后台直跑方式长期托管宿主。

完整背景见仓库（不在便携包内）`docs/analysis/pitfalls.md` 的「AI/自动化会话改代码后如何重载宿主」一节。

## 便携包（免装 Python）

```
my-service-x.y.z\
├── start.cmd        ← 双击启动（后台无窗口）
├── stop.cmd         ← 停止
├── runtime\         ← 内嵌 Python 3.12，不用另装
├── src\ web\ run\ docs\guide\
├── reg\             ← 注册条（你手写）
└── data\            ← 运行时数据（logs\ bak\ update\）
```

- 包根就是代码根，**没有 `app/` 层**；`.lnk` 自启指向 `src\server.py`，升级只换代码目录
- 升级：面板「检查更新」→ 下载校验 → 重启；`src/ web/ run/ docs/` 按清单替换，旧的留在 `data\bak\code-prev\` 可回滚，`reg/ data/ config.json` 不动
- 端口等配置改包根 `config.json`；想换数据位置就设置 `MY_SERVICE_HOME`
- 包里只带用户向文档（本文件 + `registration.md` + 截图），架构/设计/排坑留在仓库

## 目录与数据：哪些能删

| 路径 | 性质 | 删了会怎样 |
|:--|:--|:--|
| `reg\services\`、`reg\tasks\` | 你手写的注册条 | 服务 / 任务从面板消失 |
| `data\logs\` | 记录（登记簿 host.jsonl、异常栈 host.err.log） | 无影响，下次启动重建 |
| `data\bak\` | 备份（reg + config.json、升级前的旧代码） | 失去回滚点 |
| `data\update\` | 升级暂存 | 未生效的升级丢失，重新下载即可 |
| `data\cron_state.json` | 定时任务状态 | 任务重算下次触发时间 |

`data/` 整个删掉 = 丢 cron 状态与备份（服务注册条在 `reg/`，不受影响）；`reg/` 是事实源，删了要重写。
