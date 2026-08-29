# my-service

本机服务管理框架 —— 管理其他服务的启停、自启与跳转。早期内部版本 的整体升级版。

## 定位（务必理解的三条铁律）

1. **my-service 只是框架**：`src\` 内永不出现任何服务的业务源码。
2. **注册 = opt-in 声明**：服务文件夹里放一份 `manifest.json` 才会被纳管；
   没有该文件的服务（可以有任意多个）与 my-service 完全无关，互不干扰。
3. **宿主不进服务列表，但有专属自操作通道**：my-service 自身不写 manifest
   （避免"编排者杀自己/自启套娃"的自举悖论），因此服务列表里永远看不到它。
   但 header 右上角提供 `⟳重启宿主 / ■停止宿主` 专用按钮（走 `/api/host/*` 独立
   端点，非服务通道）；命令行与开机则由 `entry\start|stop|restart.ps1` 和计划任务
   `StartMyService` 负责。二者都不会把 my-service 变成"自己的一个服务"。

## 目录结构

```
my-service\
├─ README.md                  # 本文档
├─ config.json                # host/port/services_root 等
├─ entry\                     # ★ 用户操作入口：每个文件都可直接运行
│   ├─ start.ps1              # PowerShell 运行 → 启动宿主（幂等）
│   ├─ stop.ps1               # → 停止宿主（连带收割全部被管服务）
│   ├─ restart.ps1            # → 重启宿主
│   ├─ svc.ps1                # 被管服务 CLI（带参数，终端/AI 会话用）
│   ├─ install_autostart.ps1 / uninstall_autostart.ps1  # 自启注册/注销
│   └─ Dashboard.url          # 双击 → 直接打开管理面板
├─ logs\                      # 运行时产物（两档模型约定）：宿主日志 + pid
│   ├─ service.log / service.err.log               # 宿主自身日志（只有启停事件）
│   └─ service.pid
└─ src\                       # 全部实现细节
    ├─ server.py              # 组装入口（含 HostController 自管理）
    ├─ ctl.ps1                # 内部引擎（entry\ 入口与运维脚本的底层，勿直接调用）
    ├─ core\
    │   ├─ scanner.py         # 扫描 services_root 下 */manifest.json
    │   ├─ runner.py          # 进程原子操作：启动/树杀/健康检查
    │   ├─ state.py           # Manager：状态机 + monitor 秒级观测收敛
    │   └─ api.py             # REST API + web 静态托管
    └─ web\                   # Dashboard（原生 JS 零依赖）
```

## 状态机

`stopped → starting → running → stopping → stopped`，任何异常进 `failed(附原因)`。
monitor 线程每秒以健康探测校正状态并强制超时收敛——杜绝"starting 永久残留"类问题。

## manifest.json 协议

```jsonc
{
  "name": "test_service1",
  "enabled": true,                 // false = 暂时停用（不参与编排）
  "display": "测试服务一",
  "description": "py 入口示例",
  "entry": "start.py",             // 支持 .py/.ps1/.cmd/.bat/.exe
  "args": [],
  "cwd": ".",
  "mode": "managed",               // managed=宿主托管进程树 | detached=v2 预留
  "health": { "type": "port", "port": 19001, "timeout_sec": 20 },
                                   // type: port|http|process|none
  "url": "http://127.0.0.1:19001", // 可选：服务自带网页，Dashboard 一键 ↗
  "env": {},
  "autostart": true                // 权威值存此，前端开关回写这里
}
```

停止方式：默认 `taskkill /T /F` 整树收割；可另配 `stop_script`
先执行再收树。**每个服务的 stdout/stderr 写它自己的
`<服务文件夹>\logs\service.log[.err]`**（遵循 services 总目录两档约定），
Dashboard 日志窗照常尾读；宿主自身事件在 `my-service\logs\service.log`。

## 用户操作接口

| 层 | 入口 |
|:--|:--|
| Web Dashboard | http://127.0.0.1:1236 （一行一服务：状态灯/启停/↗跳转/日志窗/自启开关；header 右上含宿主运行时长 + 重启/停止宿主按钮） |
| 用户操作入口 | `entry\start.ps1`、`entry\stop.ps1`、`entry\restart.ps1`（PowerShell 运行；幂等）、`entry\Dashboard.url`（双击开面板） |
| 服务 CLI | `.\entry\svc.ps1 ls\|log <n>\|start\|stop\|restart <name>` |
| REST API | `GET /api/services`、`GET /api/host`(宿主 pid/uptime)、`GET /api/logs/{svc}?n=`、`GET /api/host-log?n=`、`POST /api/services/{svc}/start\|stop\|restart`、`POST /api/config/{svc}`、`POST /api/host/stop\|restart` |
| 注册服务 | 在 `C:\path\to\<名>\` 放带 manifest.json 的文件夹；注销 = 删文件或 `"enabled": false` |

## 快速上手

```powershell
cd C:\path\to\my-service
# 图形入口：直接打开 entry\ 文件夹，运行 start.ps1，或双击 Dashboard.url
pwsh -File .\entry\start.ps1             # 命令行起宿主
start http://127.0.0.1:1236              # 打开面板
.\entry\install_autostart.ps1          # 登录自启（一次性，需 gsudo 提权）
```
