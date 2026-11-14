# 排坑记录

> 状态：已解决问题归档 ｜ 每条保留根因与验证数据，避免重蹈

## 按需服务启动被「无监听端口探测」拖慢数秒（已解决 2026-09-03）

**现象**：on_demand 服务点开冷启动 6.2 秒，其中约 4 秒花在「探测一个还没起来的服务端口」。

**根因**：本机由 clash 类 TUN / 安全软件的流量过滤，把「连一个无监听的端口」这种本应瞬时返回"拒绝"的操作，拖成**每地址 2 秒**才超时；`localhost` 解析成 IPv6+IPv4 两个地址 → 一次防双开探测就 4 秒。纯 `socket` 直连同样慢，属系统层，非代码问题。

**解决**：

1. 防双开（`external_alive`）与 starting 判定改用 `runner.port_listening()` 快速端口探测（50ms 超时），不再用 urlopen 做可达判断——真服务 12ms 内必答，空门 50ms 即放弃。
2. 健康 `url` 的 host 按服务实际监听栈选（见 [design/registry.md](../design/registry.md#健康-url-的-host-选择实测教训双向)），避免探测先敲一个服务没监听的地址。
3. 健康探测统一走 `runner._NO_PROXY_OPENER`（`ProxyHandler({})`），强制绕过代理直连本机（防御性加固）。

**效果**：冷启动 6245ms → 646ms（探测仅剩 ~64ms，其余是进程冷启动的固有开销）。

## 登录自启黑窗（已解决 2026-08-30）

**现象**：登录触发自启时闪一个无内容黑窗，内含一行 `pid=xxxx`（实为 `ctl.ps1` 的 `Write-Host` 输出在任务外层 pwsh 的控制台里）。

**根因**：计划任务以「只在用户登录时运行」（InteractiveToken）运行——pwsh 作为控制台程序必须借用户桌面开一个 conhost 窗口短暂显示，哪怕只活 1-2 秒。

**试错路径（勿重蹈）**：

1. `pwsh -WindowStyle Hidden` —— 实测无效，窗口仍闪。
2. wscript + vbs 包装（SW_HIDE）—— 能根治，但引入新文件类型、链路变复杂，已弃用并撤销。

**曾用方案——S4U 后台任务**：把任务主体改成「不管用户是否登录都要运行」的 S4U 登录类型（后台房间：逻辑上无窗可弹）。改动仅在 `run/install_autostart.ps1` Logon 分支。要点：触发器仍 `AtLogOn`；S4U 不存密码 → 无网络凭据；RunLevel 必须 Limited。

**S4U 阶段（2026-08-30 起）**：S4U 跑在非交互窗口站，无窗但脚本操作别扭。

**启动文件夹阶段（2026-09-05 起）**：改放 `%APPDATA%\...\Startup\start-my-service.cmd`（GBK）：

```cmd
@echo off
start "" /min pwsh -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "C:\path\to\my-service\run\start.ps1" start
```

- 登录时由启动体验执行，进程跑在**当前用户交互会话** → `svc.ps1` / `ctl.ps1` / 面板直接可操作，无凭据限制、无 session 0 概念。
- **黑窗仍在**：`-WindowStyle Hidden` 只隐藏 pwsh，管不住 `start` 自身开的 cmd 窗口，实机能感知到闪一下。
- 计划任务方案（S4U / SYSTEM-AtStartup / nssm 服务）保留为 `install_autostart.ps1` 的 `-Mode` 备选。

**无窗口阶段（2026-11-14，已被下一节取代）——vbs + pythonw**：`start /min` 的窗口根治不了，改为启动文件夹放 `.vbs`：

```vbs
' my-service 开机自启（无窗口）
CreateObject("WScript.Shell").Run """<pythonw.exe>"" ""<app>\src\server.py""", 0, False
```

- `wscript` 以 SW_HIDE(0) 启动 `pythonw.exe`（GUI 子系统，本身无控制台）→ 登录时**完全无黑窗**
- 不依赖 pwsh；解释器优先包内 `runtime\pythonw.exe`（`-WithRuntime` 打包时），否则用系统同名 pythonw
- 面板「设置 → 开机自启」直接写/删该 vbs（`HostController.set_autostart`）；旧 cmd 自动清理，避免双启动
- 卸载 = 删除该 vbs（`uninstall_autostart.ps1` 一并清理计划任务）

**快捷方式阶段（2026-11-14 起，现行）——.lnk + pythonw**：vbs 其实是多余的一层。

```
%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\start-my-service.lnk
  TargetPath       : <pythonw.exe>            （优先包内 runtime\pythonw.exe，否则系统 pythonw）
  Arguments        : "<app>\src\server.py"
  WorkingDirectory : <app>\src
```

- **黑窗的真正原因是 cmd/conhost**，`pythonw.exe` 是 GUI 子系统、本身没有控制台 → 登录时无黑窗，不需要 `SW_HIDE` 技巧
- 少一层 `wscript`：脚本宿主是杀软重点关照的对象，`.lnk` 由系统外壳直接解析；用户右键属性就能看清目标与参数
- 写 lnk 借助系统自带 `powershell.exe` + `WScript.Shell` COM（**只在安装时用一次**，登录执行时不依赖任何脚本宿主）
- 面板「设置 → 开机自启」写/删该 lnk；启用时顺带清理历史 vbs/cmd，避免双启动
- 代价与 vbs 相同：存的是绝对路径，包目录搬家后需重装自启

**精简（2026-11-14）——只保留一种**：日常只用启动文件夹自启，故删除 `install_autostart.ps1` 里的 Logon / Boot / Nssm 三个分支。三种高级模式的历史用法留档于此，需要时手工执行：

```powershell
# Logon：登录触发、S4U 后台无窗（需管理员 / gsudo）
$act = New-ScheduledTaskAction -Execute 'wscript.exe' `
    -Argument '"%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\start-my-service.vbs"'
Register-ScheduledTask -TaskName StartMyService -Action $act `
    -Trigger (New-ScheduledTaskTrigger -AtLogOn) `
    -Principal (New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType S4U) `
    -Settings (New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero))

# Boot：SYSTEM 开机任务（Session 0，需提权；无法脚本关停）
#   同上，改用 -Trigger (New-ScheduledTaskTrigger -AtStartup)
#   与 -Principal (New-ScheduledTaskPrincipal -UserId 'SYSTEM' -RunLevel Highest)

# Nssm：注册为 Windows 服务
nssm install MyService "C:\path\to\runtime\pythonw.exe" "C:\path\to\src\server.py"
nssm set MyService AppDirectory "C:\path\to\src"
```

## AI/自动化会话改代码后如何重载宿主（实测 2026-09-08）

**现象**：AI 会话里启动的宿主，会随该会话结束而消失（面板打不开、定时任务停摆），而用户自己双击 `run/start.ps1` 起的宿主能稳定跑一整天（实测 09:29→20:09 无中断）。

**根因**：agent 的 shell 工具把命令包在 Windows **Job Object** 里，命令/会话结束时连带清理 Job 内全部进程。`Start-Process` 的分离、`DETACHED_PROCESS`、`CREATE_NEW_PROCESS_GROUP` 都只脱离**父进程**，**脱离不了继承的 Job 边界**——所以 agent 无能力生成一个真正与自己无关的常驻进程。

**正确工作流（关键：第一代必须由用户起）**：

1. 用户起第一代：双击/运行 `run/start.ps1`，或靠开机自启（启动文件夹）。
   - 判据：`Get-CimInstance Win32_Process -Filter "ProcessId=<pid>"` 的命令行是**绝对路径** `C:/path/to/my-service/src/server.py`（`ctl.ps1` 的 `Start-Process` 写法）；agent 直跑的是相对路径 `src/server.py`，一看便知是谁起的。
2. 之后 agent 改完代码要重载，一律走 `POST /api/host/restart`（等价面板 `⟳ 重启`）——新进程由旧宿主自己派生，仍是用户那一代的后代，agent 会话结束不影响它（实测：31912 → 32296 换代成功，跨命令边界存活，`cron_state.json` 的 next_run 原样恢复）。
3. **禁止** agent 用 `python src/server.py` 后台直跑方式长期托管宿主——那只会造出一个随会话死亡的实例，把"用户又得手动重启"的代价转嫁给用户。

> 同理适用于被管服务：agent 需要重启某个服务时，用 `POST /api/services/{svc}/restart` 让宿主去做，而不是自己 `Popen` 一个进程。
