# my-service 示例环境（demo）

把仓库源码复制到一个临时目录，用**独立端口 + 示例数据**起一个演示实例，
不动你本机真实的 `reg/`、`data/`、`logs/`。

## 用法

```powershell
pwsh -File .\examples\demo\start-demo.ps1            # 起在 http://127.0.0.1:1246
pwsh -File .\examples\demo\start-demo.ps1 -Port 1256 # 换端口
pwsh -File .\examples\demo\start-demo.ps1 -Stop      # 停止 demo 宿主与示例服务
```

默认工作目录：`%TEMP%\my-service-demo`（可用 `-WorkDir` 指定）。

## 示例内容

| 注册条 | 形态 | 演示什么 |
|:--|:--|:--|
| `demo-console` | `on_demand` | 点开才启动、空闲回收；带 `/health` 自报 service |
| `demo-worker` | `managed` + `autostart` | 宿主启动时自动拉起，端口探测健康 |
| `demo-offline` | `managed` | 未启动的 `stopped` 态与错误信息 |
| `demo-heartbeat` | `task` | 每 30 秒跑一次脚本，面板可看结果与「立即运行」 |

示例服务本体在 `services/demo_app.py`（带健康端点的极小 HTTP 面板）与
`services/demo_task.py`（追加一行日志）。

## 注意

- demo 实例用的是临时目录，删掉即恢复原状；不会碰真实实例的数据。
- demo 里注册的端口是 33510 / 33511 / 33512，若与本机其它程序冲突，改注册条里的端口即可。
- 停止顺序：先在面板里停掉示例服务（或直接 `-Stop` 强制结束），再停宿主。
