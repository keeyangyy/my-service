# 使用指南

> 状态：Accepted ｜ 面向：日常操作、终端 / AI 会话、脚本调用 ｜ 版本口径：0.19+

## 环境要求

| 项目 | 要求 |
|:--|:--|
| 操作系统 | Windows 10 / 11 |
| Python | 3.10 及以上，需在 `PATH` 中（发行包可用 `-WithRuntime` 自带解释器） |
| PowerShell | 推荐 7（脚本以 `pwsh` 验证）；5.1 可运行基础启停 |

## 四层入口

| 层 | 入口 |
|:--|:--|
| Web Dashboard | `http://127.0.0.1:1236` |
| 人的入口 | `run/start.ps1`、`run/stop.ps1`、`run/restart.ps1`（幂等）、`run/Dashboard.url`（首次启动时按本机 config 生成，双击开面板；不入库） |
| 服务 CLI | `python src/service_cli.py ls \| crons \| cron-run <名> \| events [name] [n] \| host-err [n] \| svc-start \| svc-stop \| svc-restart <name>` |
| REST API | 见下节「完整接口表」 |

> `service_cli.py events` 读的是启停登记簿；服务业务日志请去服务家 `logs/`。

## 快速上手

```powershell
cd C:\path\to\my-service
# 图形入口：直接打开 run/ 文件夹，运行 start.ps1，或双击 Dashboard.url（首次 start 时自动生成）
pwsh -File .\run\start.ps1               # 命令行起宿主
start http://127.0.0.1:1236              # 打开面板
.\run\install_autostart.ps1              # 安装开机自启（启动文件夹 .lnk → pythonw，无窗口）
```

自启是启动文件夹里的 `start-my-service.lnk`（目标 `pythonw.exe` + `src\server.py`），登录时不弹窗、不依赖 pwsh；面板右上角的「自启」开关等价。

卸载自启用 `uninstall_autostart.ps1`；需要计划任务 / Nssm 这类高级方式时，做法见仓库
[排坑记录](https://github.com/keeyangyy/my-service/blob/master/docs/analysis/pitfalls.md)。

## 配置（config.json）

`config.json` 放在包根，**可选**：不存在时使用内置默认值；面板「设置」里改完会立即生效并自动写回该文件。

| 字段 | 默认值 | 说明 |
|:--|:--|:--|
| `server.host` / `server.port` | `127.0.0.1` / `1236` | 控制面监听地址与端口 |
| `cors.local_only` | `true` | 仅向本机来源网页授予跨源权限 |
| `cors.allow` | `[]` | 额外放行的跨源来源（主机名 / IP / CIDR） |
| `audit.retention_days` | `7` | 事件保留天数，`0` 表示不清理 |
| `backup.keep` | `7` | `data/bak/` 保留份数，`0` 表示不自动清理 |
| `update.mirrors` | 内置 4 个代理前缀 | 更新检查/下载的加速源；`[]` = 只用官方 GitHub |
| `advanced.reg_dir` | `reg` | 注册条根目录 |
| `advanced.monitor.fast_sec` / `idle_sec` / `panel_grace_sec` | `1` / `10` / `15` | 状态轮询间隔与面板活跃宽限 |
| `advanced.scheduler.tick_sec` | `10` | 调度器检查间隔 |

数据目录默认就是包根；设置环境变量 `MY_SERVICE_HOME` 可把 `data/` 与 `reg/` 指到别处。

### 要在局域网里用

默认只监听回环，从别的机器访问需要同时做两件事：

```json
{
  "server": { "host": "0.0.0.0", "port": 1236 },
  "cors": { "local_only": true, "allow": ["my-laptop", "100.64.0.0/10"] }
}
```

1. `server.host` 改为 `0.0.0.0`（或具体网卡地址）；
2. `cors.allow` 列出允许的浏览器来源，支持主机名、精确 IP 与 CIDR。

注意 CORS 只约束浏览器跨源，**不约束 curl / 脚本**：任何能连到端口的设备都能直接调接口。
真正的边界在网络层，建议用防火墙只放行 Tailscale 或局域网网段。

## 完整接口表

| 方法 | 路径 | 说明 |
|:--|:--|:--|
| GET | `/health` | 宿主存活 |
| GET | `/api/host` | 宿主 pid / uptime / version / autostart |
| GET | `/api/services` | 服务列表（含状态、端口、关联任务） |
| GET | `/api/services/{name}` | 单服务 |
| GET | `/api/audit?n=&svc=&fail=1` | 启停登记簿 |
| GET | `/api/host-err?n=` | 宿主异常栈 |
| GET | `/api/crons` | 定时任务列表 |
| GET | `/api/settings` | 编排器全局设置 |
| GET | `/api/update/check` | 检查新版本（只读清单、不下载） |
| POST | `/api/services/order` | `{"names":[...]}` 拖拽排序 |
| POST | `/api/services/{svc}/start\|stop\|restart[?via=cli]` | 启停 |
| POST | `/api/services/{svc}/config` | `{"idle_timeout_sec": N}`（仅 on_demand） |
| POST | `/api/crons/order` | `{"names":[...]}` 拖拽排序 |
| POST | `/api/crons/{name}/run[?via=cli]` | 手动触发 |
| POST | `/api/crons/{name}/config` | `enabled` / `state` / `catch_up` / `timeout_sec` / `schedule` 任意组合 |
| POST | `/api/config/{svc}` | `{"autostart": bool}` |
| POST | `/api/settings` | `cors` / `audit` / `backup` / `update` / `advanced` 任意组合，写回 `config.json` |
| POST | `/api/host/autostart` | `{"enabled": bool}` 开关开机自启 |
| POST | `/api/host/stop\|restart` | 宿主自身生命周期 |
| POST | `/api/audit/clear` | `{"svc":"名"}` 清该名事件 / `{}` 清全部 |
| POST | `/api/backup` | 立即备份 `reg/` + `config.json` |
| POST | `/api/update/apply` | 下载 + 校验 + 暂存，随后自动重启生效 |

失败响应统一为 `{"ok": false, "error": "原因"}`，HTTP 状态码同时反映类别（400 参数 / 404 未知对象 / 500 内部错误）。
启停接口带 `?via=cli` 时，登记簿会把它标为 CLI 发起，便于区分面板点击与脚本调用。

## 定时任务

在 `reg/tasks/<任务名>.json` 声明，协议与排查见 [注册一个服务](registration.md) 的「定时任务」一节。
面板支持行内修改时刻与间隔，并展示上次执行结果。

三个常见坑：`task` 的 `state` 默认 `disabled`（要手动开）；`weekly.days` 为空 = 不调度；
错过计划时 `catch_up: false` 只记一条 `cron_skip` 并顺延。

## 升级与回滚

面板顶部「⟳ 更新」：先只读检查清单（版本 / 大小 / 更新说明 / 实际更新源），确认后才下载。

- 校验：包体 sha256 与大小必须与清单一致；清单本身有 Ed25519 签名，客户端内置公钥验签，**缺失或验签失败一律拒绝更新**；
- 生效：替换 `src/`、`web/`、`run/`、`docs/` 与 `README.md`、`CHANGELOG.md`、`LICENSE`，随后重启宿主；`reg/`、`data/`、`config.json` **不受影响**；
- 失败：自动回滚，原因写 `data/update/last-error.json`；上一版代码留在 `data/bak/code-prev/`，需要时手工搬回。

c675 这类直连 GitHub 不通的机器，靠内置的 4 个代理前缀（`update.mirrors`）走加速源，某个源失败会自动换下一个。

## 卸载

```powershell
pwsh -File .\run\stop.ps1                 # 1. 停宿主
pwsh -File .\run\uninstall_autostart.ps1  # 2. 卸载开机自启（删启动文件夹的 .lnk）
```

3. 需要保留配置就先备份 `reg/` 与 `config.json`，然后整目录删除即可。
   `data/` 里是运行时数据（登记簿、备份、升级暂存），删除不影响其它机器。

## 注意（AI / 自动化会话）

agent 启动的宿主会随会话结束消失（Windows Job Object 连带清理）。正确做法：

- 第一代宿主由用户双击 `run/start.ps1` 或开机自启拉起；
- agent 改完代码后用 `POST /api/host/restart`（等价面板 `⟳ 重启`）重载，新进程由旧宿主派生，能跨会话存活；
- 禁止 agent 用 `python src/server.py` 后台直跑方式长期托管宿主。

完整背景见仓库
[排坑记录](https://github.com/keeyangyy/my-service/blob/master/docs/analysis/pitfalls.md)
的「AI/自动化会话改代码后如何重载宿主」一节。

## 发行包（下载即用）

```
my-service\
├── run\start.ps1    ← 双击启动（需要系统 Python 3.10+）
├── run\stop.ps1     ← 停止
├── src\ web\ run\ docs\guide\
├── reg\             ← 注册条（你手写）
└── data\            ← 运行时数据（logs\ bak\ update\）
```

- 依赖：目标机器需要 Python 3.10+；要连同解释器一起分发，打包时加 `-WithRuntime`
- 包根就是代码根；`.lnk` 自启指向 `src\server.py`，升级只换代码目录
- 端口等配置写在包根 `config.json`（不存在时用内置默认，面板设置保存会自动创建）；想换数据位置就设置 `MY_SERVICE_HOME`
- 包里只带用户向文档（本文件 + `registration.md` + 截图），架构/设计/排坑留在仓库

## 目录与数据：哪些能删

| 路径 | 性质 | 删了会怎样 |
|:--|:--|:--|
| `reg\services\`、`reg\tasks\` | 你手写的注册条 | 服务 / 任务从面板消失 |
| `data\logs\` | 记录（登记簿 host.jsonl、异常栈 host.err.log） | 无影响，下次启动重建 |
| `data\bak\` | 备份（reg + config.json、升级前的旧代码） | 失去回滚点 |
| `data\update\` | 升级暂存 | 未生效的升级丢失，重新下载即可 |
| `data\pids\` | 框架写的 pid 凭证（宿主与它拉起的实例） | 无影响，下次启动重建 |
| `data\cron_state.json` | 定时任务状态 | 任务重算下次触发时间 |

`data/` 整个删掉 = 丢 cron 状态与备份（服务注册条在 `reg/`，不受影响）；`reg/` 是事实源，删了要重写。
