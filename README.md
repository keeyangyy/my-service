# my-service

局域网/本机通用服务管理框架 —— 早期内部版本 的整体升级版。

## 定位与铁律

- **my-service 只是框架**：本目录永不出现任何服务的业务源码。
- **每服务一个独立文件夹**：放在 `C:\path\to\<名字>\`，含一份 `manifest.json` 即完成注册。
- 框架与服务之间唯一的认知通道 = `manifest.json` 协议。

## 架构

```
my-service\            # 宿主框架（本目录）
├─ server.py           # 组装入口（python 直跑或经 run.ps1）
├─ core\
│   ├─ scanner.py      # 扫描 services_root 下 */manifest.json → Spec
│   ├─ runner.py       # 进程原子操作：构建命令/启动/树杀/健康检查
│   ├─ state.py        # Manager：状态机 + monitor 秒级观测收敛
│   └─ api.py          # REST + web 静态托管
├─ web\                # Dashboard 一行一服务（原生 JS 零依赖）
├─ run.ps1             # 宿主启停壳 start|stop|status|restart
├─ svc.ps1             # 服务管理 CLI 薄壳（转调 REST API）
└─ install/uninstall_autostart.ps1   # 自启注册（Logon|Boot|Nssm 参数化）
```

状态机：`stopped → starting → running → stopping → stopped`，异常一律 `failed(附原因)`。
monitor 线程每秒用健康检查校正观测态，超时强制收敛——杜绝 早期内部版本 曾见的
"starting 标记永久残留"问题。

## manifest.json 协议

```jsonc
{
  "name": "test_service1",
  "enabled": true,                 // false 时不出现在编排里
  "display": "测试服务一",
  "description": "py 入口示例",
  "entry": "start.py",             // 支持 .py/.ps1/.cmd/.bat/.exe
  "args": [],
  "cwd": ".",
  "mode": "managed",               // managed=宿主托管进程树 | detached=v2 预留
  "health": { "type": "port", "port": 19001, "timeout_sec": 20 },
                                   // type: port|http|process|none
  "env": {},
  "autostart": true                // 权威值存此，前端开关回写这里
}
```

停止模式：`managed` 直接 taskkill /T /F 收割整棵进程树；
manifest 可另配 `stop_script`（如 `stop.cmd`），stop 时先执行它再收树。
日志由宿主统一接管：`my-service\logs\<svc>\service.log`。

## 用户操作接口

| 层 | 入口 |
|:--|:--|
| Web Dashboard | http://127.0.0.1:1236 （一行一服务：状态灯/启停/日志窗/自启开关） |
| REST API | `GET /api/services`、`GET /api/logs/{svc}?n=`、`POST /api/services/{svc}/start\|stop\|restart`、`POST /api/config/{svc}`、`GET /health` |
| CLI | `.\run.ps1 start|stop|status|restart`（宿主）；`.\svc.ps1 ls|log <n>|start|stop|restart <name>`（服务） |
| 注册/注销 | 新增 = 放带 manifest.json 的文件夹；注销 = 删除或 `"enabled": false` |
| 自启 | `install_autostart.ps1`（默认登录触发；`-Mode Boot/Nssm` 可切换） |

## 配置

`config.json`：`host`(默认 127.0.0.1) / `port`(1236) / `services_root`(..)
/ `ignore_dirs` / `log_dir` / `log_max_bytes`。

## 快速上手

```powershell
cd C:\path\to\my-service
.\run.ps1 start                      # 起宿主
start http://127.0.0.1:1236          # 打开面板
.\svc.ps1 ls                         # 命令行看一眼
.\install_autostart.ps1              # 登录自启（一次性）
```
