# run/ —— 人的操作入口

> 这里只放"给人双击用"的入口。启停逻辑统一在 `src/service_cli.py`，本目录的 ps1 都只是薄壳。

| 文件 | 双击效果 | 说明 |
|:--|:--|:--|
| `start.ps1` | 启动宿主 | 已在运行则提示 pid，不重复启动；启动后按需生成面板快捷方式 |
| `stop.ps1` | 停止宿主 | 只停宿主，被管服务继续运行（生命周期独立） |
| `restart.ps1` | 停止再启动 | 等价 `stop` → `start` |
| `install_autostart.ps1` | 安装开机自启 | 写 `%APPDATA%\...\Startup\start-my-service.vbs`（wscript + pythonw，无窗口）；面板 ⚙ 设置也能开关 |
| `uninstall_autostart.ps1` | 卸载自启 | 清理 vbs / 历史 cmd / 计划任务 |
| `Dashboard.url` | 浏览器打开面板 | 首次启动时按本机 `config.json` 生成，不入库 |

## 便携包里的对应入口

便携包（`dist/my-service-<版本>/`）不依赖 pwsh，用包根的两个 cmd：

| 文件 | 双击效果 |
|:--|:--|
| `start.cmd` | 启动（先做自更新替换，再起 python） |
| `stop.cmd` | 停止 |

## 不在这里的东西

- `src/service_cli.py`：真正的启停引擎（`start` / `stop` / `restart` / `status` / `shortcut`），可单测
- `src/svc.ps1`：命令行客户端（`ls` / `start <名称>` / `events` …），只调 HTTP API，不启动宿主
- `src/server.py`：宿主本体

## 为什么入口这么多

| 场景 | 为什么不能共用一套 |
|:--|:--|
| 源码模式启停 | 有 pwsh，用 ps1（`run/*.ps1`） |
| 便携包启停 | 用户可能没装 pwsh，用 cmd（包根 `start.cmd` / `stop.cmd`） |
| 开机自启 | cmd/ps1 都会闪控制台，只有 vbs + pythonw 能无窗口 |

三套壳共用同一份逻辑（`src/service_cli.py`），所以改行为只需改一处。