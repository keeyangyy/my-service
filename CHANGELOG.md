# 更新日志

本文件记录 my-service 的版本发布历史。版本号在 `src/server.py` 的 `VERSION` 常量维护，与 git tag 同步。

## v0.2（2026-09-05）

日志架构重构：**三条流彻底分离，一个文件只有一个作者**。框架收回伸进服务目录的手，转为零侵入纳管。

### 破坏性变更
- **框架不再收集被管服务的 stdout/stderr**：`runner.start_process` 一律 `DEVNULL`。服务日志由服务自己写（它家 `logs\`），纳管与否不改变其日志形态。
  → 接入要求变硬：常驻服务必须自写文件日志且自轮转（规范与 python 片段见 `C:\path\to\README.md`「日志合规」）
- **框架不再向服务目录写任何文件**：被管进程 pid 记账从 `<服务 path>\logs\service.pid` 迁到 `my-service\logs\pids\<服务名>.pid`
- **接口换血**：`GET /api/logs/{svc}`、`GET /api/host-log` 移除，新增 `GET /api/audit?n=&svc=&fail=1`（登记簿）与 `GET /api/host-err?n=`（宿主异常栈）；`POST /api/services/{svc}/start` 支持 `?via=` 记来源
- **宿主日志文件改名**：`logs\service.log` → `logs\host.jsonl`（结构化事件流）+ `logs\host.err.log`（异常栈）+ `logs\host.pid`
- **CLI**：`svc.ps1 log <name>` → `svc.ps1 events [name] [n]`，新增 `host-err [n]`

### 框架内核
- 新增 `src\core\audit.py`：控制面登记簿，固定 7 字段 JSONL（`ts/svc/act/ok/dur_ms/pid/note`）、`ACTS` 事件字典、5 MB × 3 自轮转、写盘失败静默（登记簿不得拖垮编排）
- `state.Manager` 全量埋点：`start` 终态（含耗时与 `via` 来源）、`stop`/`idle_reclaim`、`crash`、`health_flip`、`adopt_external`、`autostart_skip`、`reject_double`、`spawn_fail`、`spec_invalid`
- 健康探测带 `User-Agent: my-service-health`，供服务侧识别机器流量并对其静默访问日志
- 修 `state._tick` 中 `healthy, why, last_active = runner.check_health(...), None` 的三元解包隐患（managed + port/http 型服务一旦上线即会炸穿 monitor 循环）
- `ctl.ps1` 改为优先读 `logs\host.pid`（校验进程存活），CIM 命令行匹配降为兜底

### Web Dashboard
- 卡片「日志」→「事件」：呈现该服务的启停事件时间线（动作中文化、失败标红、耗时/来源/pid）
- header 新增 `▤ 登记簿` 全局抽屉（含宿主事件，`svc=null` 显示为 `(host)`）
- 修三处过时路径提示：`run\start.ps1` → `entry\start.ps1`、`run\service.err.log` → `logs\host.err.log`

### 接入实例
- `your-service` 补齐自管日志（`RotatingFileHandler` 2 MB × 5；`/health` 不再灌访问日志；未捕获异常双写文件与 stderr）
- `reg\some-service.json` 的 `path` 已指向 `C:/path/to/your-service`，README 样例同步（旧文档仍写便携目录，改 `path` 即换日志落点，属高频踩坑点）

### 已知遗留（明确不在本次范围）
- detached 软件的本体日志留在程序自家（some-service 真日志 = `%LOCALAPPDATA%\SomeService\some-service.log`，38 MB 无轮转），框架不代理读取
- 官方 `some-service\start.ps1` 将本体输出重定向到 TEMP 随机文件后删除，那份输出注定丢失

---

## v0.1（2026-08-30）

首个正式版本。本机服务管理框架（早期内部版本 的整体重写版），纯 Python 标准库 + 原生 JS 零依赖，`127.0.0.1:1236`。

### 框架内核
- 四模块分层：`scanner`（注册中心扫描）/ `runner`（进程原子操作）/ `state`（状态机 Manager）/ `api`（REST + 静态托管）
- 状态机 `stopped → starting → running → stopping → failed`，monitor 线程每秒以健康探测校正并**强制超时收敛**（根治 早期内部版本 的 `starting` 永久残留类问题）
- 两种托管模式：`managed`（框架 Popen 本体 + `taskkill /T` 树杀）/ `detached`（点火器型：官方启停脚本，死活只信健康探测）
- **防双开**（`external_alive` 统一判定）：port/http 走健康探测、process 走 pidfile 存活判据；冷启动检测到已有实例即拒绝重复拉起；自启遇已在运行则**跳过并归位 running**；detached 假失败（实例活着但超时）自动自愈
- **路径安全**：注册条 `path` 钉死服务真实家（cwd 敏感软件防漂移空库——some-service 实弹教训）；`adapters\<svc>\` 可选转接层（相对路径优先命中，官方脚本回退）
- 原子写注册条（临时文件 + `os.replace`）；日志句柄全生命周期回收

### 注册中心
- `reg\<服务名>.json` 为**唯一发现源**：`path` 指向任意位置的服务，entry/cwd/stop_script 相对 path 解析；写文件即注册、删除或 `enabled:false` 即注销
- 注册条校验（无效标 invalid 并附原因）、跨服务健康端口冲突检测

### Web Dashboard
- 一行一服务单行卡片；GitHub Dark 配色体系、胶囊状态徽章（光晕点 + 过渡态呼吸）、描边 pill 按钮、毛玻璃 sticky header、toast
- **整行点击直达服务网页**（`url` 字段；键盘 Enter 可达；行内控件 stopPropagation 防误触）
- 每服务日志窗（展开跟随轮询重绘、贴底自动滚、单卡互斥展开）；自启开关即点即回写
- header：宿主运行时长（区别于单服务 uptime）+ `⟳重启 / ■停止` 宿主专属通道（`/api/host/*`）

### 宿主自管理与部署
- 目录三区分：`entry\`（人可直跑入口）/ `src\`（实现 + 程序化接口）/ `logs\`（宿主日志 + pid）
- `entry\start|stop|restart.ps1` 幂等入口；`src\svc.ps1` CLI；`install_autostart.ps1` 计划任务登录自启（AtLogOn，已实测真实开机链路）
- 宿主 restart 无缝：detached 重建 + **端口重试绑定**（新旧进程交接零竞态，实测 pid 更替全程不断服）

### 接入实例
- `reg\some-service.json`：AI agent 通信网关（8090），detached 直指官方脚本零副本；健康探测地址用 `localhost`（IPv6 监听面教训）

---
生成自 v0.1 前的 17 次提交（`779f6d6` → `13c2f33`）。