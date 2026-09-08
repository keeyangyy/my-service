# 更新日志

本文件记录 my-service 的版本发布历史。版本号在 `src/server.py` 的 `VERSION` 常量维护，与 git tag 同步。

## v0.10.0（2026-09-08）

面板按 `mode` 分两组，服务也能行内改配置；定时任务设置面板重新分区。

### 服务区分组（纯前端按 mode 推导，注册条零改动）
- **常驻服务**（managed / detached）与**即用即走**（on_demand）各成一组，标题带数量角标
- 空组整组隐藏（含标题）；跨组拖动被拒（`dragRow.parentNode` 判定），组内照常拖
- 修坑：保存顺序改为提交**两组合并名单**——只交本组会让后端把未提及的另一组
  按字母序重排，冲掉用户刚排好的顺序

### 每服务 `⚙ 设置` 面板
- 常驻服务：只显示「关联的定时任务」；即用即走：另含「空闲回收时长」
- 自启开关按约定留在主行，不进面板
- 展开态与最新快照跨轮询保持（`svcCfgOpen` + `svcData`，点 `⚙` 立即取真值）

### 空闲回收时长可配（原来只能手改注册条）
- `Manager.update_config(name, patch)`：复用定时任务那套安全网——读原文备份 →
  应用补丁 → 原子写 → 用 Spec 重解析，invalid 则**回滚原文**，绝不留坏注册条
- 新增 `POST /api/services/{name}/config`（`{"idle_timeout_sec": N}`）
- ★ 越界值**显式拒绝**而非静默 clamp：否则注册条存 5、内存生效 60，
  文件与实际不一致且面板回弹没有解释。边界 60~86400 秒提为
  `scanner.IDLE_MIN_SEC/IDLE_MAX_SEC` 单一事实源，clamp 与校验共用
- 面板同时显示「≈ 距回收 还剩多久」（由 last_active 推算），让抽象秒数有实感

### 服务与定时任务按路径关联（新 core/linkage.py）
- 判据是**文件系统路径**：cron 注册条的 `path` 落在哪个已注册服务的 `path`
  之下（或相等）就归属它；服务目录嵌套时取最长匹配
- 为什么不用名字前缀：路径是物理事实，改名不破坏关联，前缀只是巧合
- `is_under` 按路径分量比较，`D:\svc\ab` 不会被误判为在 `D:\svc\a` 之下
- 匹配不到即「无关联」，任务继续在定时任务区独立显示；快照为服务附加 `cron_names`

### 定时任务设置面板分区（修信息架构）
- 原先「定时执行 / 间隔执行 / 超时保护 / 补跑」四个控件平铺，
  超时保护易被误读成第三种调度方式（实际已有人误解）
- 改为两个带标题的分区：**执行时机**（模式 + 时刻/间隔）与
  **异常保护**（超时保护 + 补跑），并补一行说明点明"两者都不改变执行频率"

### 按钮顺序统一（用户反馈）
- 定时任务行原为 `⚙ 设置 / 事件 / ▶ 立即运行`，与服务卡片（操作按钮在前、
  设置与事件殿后）不一致；改为 `启用 / ▶ 立即运行 / ⚙ 设置 / 事件`，两类行同序

### 测试
- 新增 `tests/test_linkage.py` 24 例（路径判定含大小写/前缀相似/嵌套最长、
  snapshot 关联字段、provider 异常兜底、update_config 全路径）
- 全量 92 例通过；浏览器实测分组渲染、组内拖拽、跨组拒绝、两面板展开均正常

## v0.9.0（2026-09-08）

控制面对本机网页开放：**判据只有"来源是否本机"，端口不限**。

- 本机任意网页（`localhost` / `127.0.0.1` / `::1`，任意端口）可调用全部接口并
  **读到返回**；响应回显 `Access-Control-Allow-Origin` + `Vary: Origin`
- 新增 `do_OPTIONS`：本机来源 `204` 并给出允许的方法与头；外站 `403` 且无 CORS 头
- 外站写接口门禁：`/api/services/*`、`/api/crons/*`、`/api/audit/clear`、
  `/api/config/*`、`/api/host/*` 一律 `403`（统一挂在 `do_POST` 入口，避免漏挂）。
  依据：浏览器跨源 POST 必定带 `Origin`，而 curl/脚本不带 → 本机程序不受影响
- 无 `Origin` 的请求（面板同源 / curl / 脚本）行为与历史**完全一致**，零回归
- 新增 `config.json` 开关 `cors_local_only`（默认 true），置 false 即退回历史语义
- `src\core\api.py` 新增 `LOCAL_HOSTS` / `WRITE_PREFIXES` 与 `_origin_of` /
  `_origin_host` / `_is_local_origin` / `_is_write_path`；`make_handler` / `serve`
  新增 `cors_local_only` 参数
- 新增 `tests/test_cors.py` 19 例（判据、响应头、预检、外站门禁、开关关闭回旧行为）
- **为什么值得做**：被管服务（如 your-service）被空闲回收后，其页面自身就是
  死掉的进程、无法自救；现在可由页面调宿主 start 原地复活，还能读到宿主的失败原因
  （"注册条无效"/"端口被占用"），不用再干等超时
- 已知边界：`file://` 页面的 `Origin` 为 `null`，按外站拒绝

## v0.8.0（2026-09-08）

面板支持拖拽排序：**服务列表与定时任务列表**都可手动调整顺序。

### 框架内核
- `state.Manager` 与 `scheduler.Scheduler` 各自新增顺序账本（`logs\service_order.json`
  / `logs\cron_order.json`，原子写；缺失或损坏退回字母序）
- 两者都新增 `set_order(names)`：只接受已注册名并去重，未提及的按字母序追加末尾
- `snapshot()` 改按拖拽顺序返回——新注册的服务/任务自动排末尾，不打乱已排好的顺序
- `refresh()` / `scan()` 自动清理已注销条目的顺序项，避免重启后复活
- 新增 `POST /api/services/order` 与 `POST /api/crons/order`（`{"names":[...]}`，非数组 400）

### Web Dashboard
- 服务卡片与定时任务行首都新增拖拽把手 `⠿`：**按住把手才开启 draggable**（整行常驻可拖
  会干扰选文字与点按钮）；拖动中实时插入换位 + 半透明高亮，松手自动保存并 toast
- 拖拽逻辑通用化（`buildDragHandle` / `bindRowDrag` / `saveOrder`），两个列表共用一套
- 服务卡片「点整行打开服务网页」与拖拽冲突已处理：拖完 250ms 内抑制补发的 click
  （实测拖动后不会误开中转页）
- 拖动期间以 `sortBusy` 冻结两个列表的轮询重绘（轮询本身继续），防 DOM 被刷掉打断手势
- 每轮按后端顺序 `appendChild` 同步 DOM（移动已有节点，不重建、事件与焦点保留）

### 按钮顺序统一（用户反馈）
- 定时任务行原为 `⚙ 设置 / 事件 / ▶ 立即运行`，与服务卡片（操作按钮在前、
  设置与事件殿后）不一致；改为 `启用 / ▶ 立即运行 / ⚙ 设置 / 事件`，两类行同序

### 测试
- 新增 `TestOrdering` 8 例（任务）+ `tests/test_state_order.py` 8 例（服务）：默认字母序、
  重排生效、未知名忽略与去重、部分列表追加、跨重启保持、新条目不打乱、注销自动清理、
  无 order 路径退化；合计 49 例全绿

## v0.7.0（2026-09-08）

定时任务支持面板行内编辑配置（此前改时间只能手编注册条）。

### 框架内核
- `scheduler.py` 新增 `update_config(name, patch)`：补丁式改 `schedule` / `timeout_sec` / `catch_up` / `enabled`
  - **写后校验回滚**：写回立即用 `CronSpec` 重解析，判无效则还原原内容并报错——绝不留坏注册条
  - `schedule` 变更时**重算 `next_run`**（否则面板显示新时间、实际仍按旧时间触发）
  - 未编辑字段原样保留（读原 JSON 打补丁，不重造注册条）
- `toggle_enabled` 改为转调 `update_config`（统一原子写回 + 校验路径）
- `snapshot()` 增出编辑控件所需的原始参数：`schedule_type` / `at` / `every_sec`

### REST API
- `POST /api/crons/{name}/config` 由单一 `enabled` 扩展为补丁端点：四字段任意组合；空补丁/类型错 400，未知任务 404，非法配置 400（含回滚说明）

### Web Dashboard
- 定时任务保持**一行卡片**：概览（状态/调度摘要/下次执行/上次结果）+ `启用`开关 +
  `⚙ 设置` + `事件` + `▶ 立即运行`；点 `⚙ 设置` 就地展开配置面板
- 配置面板内**模式用分段按钮二选一**（定时执行 / 间隔执行），比并列两个输入框更贴合
  "两种模式"的语义；切换即写回，并保留两种模式各自的最后已知值（本会话内），
  误点不会把原时间冲成硬编码默认
- 面板含时刻选择器（daily）/ 数值+单位下拉（interval）、`⏲ 超时`秒数、`补跑`开关；
  改完即保存（change 事件），失败自动回滚控件值并 toast 报错
- 展开态用 `cronCfgOpen` 跨轮询保持；轮询同步控件值时跳过聚焦中的控件（不打断输入）；
  控件回调统一读 `cronData` 最新快照，不用建骨架时的旧数据
- 超时控件标签改为「⏲ 超时保护」，悬停明确"卡死保护，不是执行频率；执行频率请用
  「间隔执行」模式"——原「超时」二字易被误读成执行间隔
- 坏注册条：`⚙ 设置` 按钮与面板内控件一律禁用
- 「上次结果」chip **去掉耗时**（挪入悬停提示）——原先 `上次 ✓ 09-07 23:08 · 2.6s` 与右侧
  启用开关挤压重叠；失败原因（`rc=N` / `超时`）不是时长，仍保留在标签上
- 任务描述从主行移到任务名悬停提示，把宽度让给状态与调度信息
- 主行 chip 允许压缩省略 + `.info` 防溢出：任何宽度下都不会越过边界压到操作区
- 主容器 `max-width` 840 → 960：实测 840 下 info 仅剩 479px 可用（内容需约 550px），
  三个 chip 全部被省略号截断；加宽后完整显示，服务卡片的描述截断同步受益
  （窄屏下 chip 优雅降级为省略号，悬停看全文）
- `:root` 加 `color-scheme: dark` 使原生时间选择器/数字步进器跟随暗色；
  开关轨道底色由 `--border` 提亮至 `#3a4358` 并加内描边（关闭态原先与卡片背景几乎同色，
  看不出是可交互控件——服务卡片的自启开关同步受益）

### 按钮顺序统一（用户反馈）
- 定时任务行原为 `⚙ 设置 / 事件 / ▶ 立即运行`，与服务卡片（操作按钮在前、
  设置与事件殿后）不一致；改为 `启用 / ▶ 立即运行 / ⚙ 设置 / 事件`，两类行同序

### 测试
- `test_scheduler.py` 新增 `TestUpdateConfig` 9 例：改时刻并重算 next_run、改间隔、超时+补跑组合、非法时刻回滚、低于下限回滚、未知类型/未知任务、enabled 补丁、无关字段保留；合计 33 例全绿

## v0.6.0（2026-09-07）

登记簿清理能力 + 版本号可见。

### 框架内核
- 登记簿自动清理：`config.json` 新增 `audit_retention_days`（默认 7 天，0 = 永不）；宿主启动时清一次、此后每小时复查；按事件 `ts` 判断，坏行/解析失败行保留不误删
- `audit.py` 新增 `clear(svc)`（按名/全清，原子写回，归档 .1/.2/.3 不受影响）与 `prune(days)`
- 新增 `POST /api/audit/clear`（`{"svc":"名"}` 清该名 / `{}` 清全部）

### Web Dashboard
- 事件窗（卡片窗与全局登记簿抽屉）右上新增「✕ 清空」按钮：确认后清空对应范围，sticky 置顶保持可见
- 左上角标题旁显示宿主版本号（动态取自 `/api/host`，随宿主更新）

### 按钮顺序统一（用户反馈）
- 定时任务行原为 `⚙ 设置 / 事件 / ▶ 立即运行`，与服务卡片（操作按钮在前、
  设置与事件殿后）不一致；改为 `启用 / ▶ 立即运行 / ⚙ 设置 / 事件`，两类行同序

### 测试
- 新增 `tests/test_audit.py` 6 例：按名清空/全清/未知名/坏行保留/按天清理/prune 边界；合计 25 例全绿

## v0.5.0（2026-09-07）

定时任务面板增强：任务开关 + 事件窗；`cron_run` note 格式简化。

### Web Dashboard
- 定时任务行新增「启用」开关滑块（同服务自启滑块交互）：权威值回写注册条 `enabled` 字段，停用即暂停调度、重新启用即恢复；停用期间不排新计划，正在跑的一轮不受影响
- 定时任务行新增「事件」按钮：展开该任务的 `cron_run/cron_skip` 时间线（登记簿按任务名过滤），与服务卡片同一套展开体系（互斥展开、贴底滚动）

### 框架内核
- `scheduler.py` 新增 `toggle_enabled`（原子写回注册条，与服务的 `toggle_autostart` 同模式）
- `cron_run` note 格式简化：去掉调度规则描述（卡片上已有）与英文来源标记；只记结果与非常规来源——定时触发无前缀（如 `rc=0`），手动触发/错过补跑以中文前缀区分（如 `手动触发，rc=0`）；`cron_skip` 同步去掉规则描述
- 新增 `POST /api/crons/{name}/config`（`{"enabled":bool}`，非布尔 400）

### 按钮顺序统一（用户反馈）
- 定时任务行原为 `⚙ 设置 / 事件 / ▶ 立即运行`，与服务卡片（操作按钮在前、
  设置与事件殿后）不一致；改为 `启用 / ▶ 立即运行 / ⚙ 设置 / 事件`，两类行同序

### 测试
- `test_scheduler.py` 新增开关用例：回写注册条、内存态同步、停用期间不排程、重新启用恢复、未知任务；合计 19 例全绿

## v0.4.0（2026-09-07）

新增定时任务支持：与常驻服务并行的第二物种——`reg\cron\` 注册、宿主内置调度线程到点执行脚本，无常驻进程、无健康检查、无状态机。

### 框架内核
- 新增 `src\core\scheduler.py`：调度器（与 monitor 并列的第二个永动线程）。扫描 `reg\cron\*.json`，每秒 tick，到点开独立线程执行 `runner.run_once`
- 新增注册目录 `reg\cron\<任务名>.json`（零侵入隔离：`reg` 的 glob 不递归，定时任务与服务注册天然分家，互不改动对方一行代码）；注册条复用服务的 `path/entry/args/cwd/env` 字段语义，新增 `schedule`（daily HH:MM / interval every_sec）、`timeout_sec`（默认 300，下限 5s）、`catch_up`（默认 false）
- `runner.py` 新增 `run_once` 一次性执行原子操作：无窗 + 新进程组 + 输出 DEVNULL（日志边界不变），超时 `taskkill /T /F` 树杀
- 调度语义：防重叠（上一轮未完不触发，跑完统一走"错过"处理，不堆积连发）；错过判定宽限 60 秒，`catch_up=true` 补跑一次、默认登记 `cron_skip` 顺延（开机绝不齐跑）；interval 新任务起算一个周期后首跑；执行超长后 next_run 统一重排
- 状态持久化 `logs\cron_state.json`（临时文件 + `os.replace` 原子写）：last_run/last_ok/last_rc/last_dur_ms/next_run，宿主重启后照单恢复
- `audit.py` 事件字典新增 `cron_run`（成功带耗时/失败给 rc/超时/启动原因）与 `cron_skip`（错过顺延）
- 宿主装配（`server.py`）：`reg\cron` 目录不存在则建空目录（用户一眼可见）；调度线程在 host_start 之后启动（首条事件因果一致约束）；host_start 登记附定时任务清单

### REST API
- `GET /api/crons`：定时任务列表（调度规则/下次执行/上次结果/invalid 原因）
- `POST /api/crons/{name}/run[?via=cli]`：手动立即触发；运行中重复触发返回 409

### Web Dashboard
- 服务列表下方新增「⏱ 定时任务」区块：状态徽章（执行中呼吸/已启用/已停用/无效）、调度规则与下次执行 chip、上次结果 chip（✓/✗ rc/超时）、「▶ 立即运行」按钮；无任务时整块隐藏
- 登记簿动作中文化同步：定时执行 / 定时跳过

### CLI
- `svc.ps1 crons`：定时任务表格（状态/调度/下次/上次结果）
- `svc.ps1 cron-run <名>`：手动触发

## v0.3.0（2026-09-06）

宿主与被管服务生命周期解耦：宿主启停不再连坐被管服务，自启方案落地文档化。

### 框架内核
- **宿主启停与被管服务生命周期解耦**：宿主 stop/restart（面板、API、Ctrl+C、`ctl.ps1`）均不再收割被管服务；`ctl.ps1` 的 `taskkill` 去掉 `/T` 只杀宿主自身。重启宿主后，继续运行的服务由端口探测 + `adopt_external` 归位 running，全程不中断。彻底停止宿主也不停服务，要停哪个用 `svc.ps1 stop <名字>`

### Web Dashboard
- 登记簿抽屉补齐基础样式（overflow/max-height/背景/边框仿 `.log-panel`），修复事件超过 300px 时内容溢出叠到服务卡片上的布局混乱
- 宿主重启/停止按钮与确认弹窗文案改为「被管服务不受影响，继续运行」，同步解耦后的真实行为

### 自启方案
- 自启方案文档化：启动文件夹为当前方案（`start-my-service.cmd`，进程跑在当前用户交互会话，脚本可直接操作），README 排坑记录补齐实践演进
- `install_autostart.ps1` 默认 `Mode` 改为 `Startup`（写启动文件夹 cmd，无需提权），`Logon(S4U)/Boot(SYSTEM)/Nssm` 保留为 `-Mode` 备选
- `uninstall_autostart.ps1` 同时清理启动文件夹 cmd + 计划任务

### 接入实例
- **your-service**：`reg\your-service.json` 新增，path 指 `C:/path/to/your-service`，entry=server.py，本机 8081
  - `mode=on_demand`：点开即启、空闲 600s 自动回收，不常驻后台
  - `health=http(127.0.0.1:8081/health)`，服务自报 `last_active` 供空闲回收
  - 纯增量：未改动框架任何源码

## v0.2.0（2026-09-05）

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