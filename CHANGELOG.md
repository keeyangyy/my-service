# 更新日志

本文件记录 my-service 的版本发布历史。版本号在 `src/core/host.py` 的 `VERSION` 常量维护，与 git tag 同步。

## v0.16.0（2026-11-15）

### 新增功能

- 服务管理 CLI 合并进 `service_cli.py`（唯一实现）
  - 新增 `ls` / `svc-start` / `svc-stop` / `svc-restart` / `crons` / `cron-run` / `events` / `host-err`
  - `src/svc.ps1` 缩为薄壳（命令名映射 + 参数透传），逻辑只保留一份
  - 服务管理命令一律走宿主 HTTP API，不直接读注册条或动进程

### 变更

- 一键升级不再区分部署形态
  - 源码工作区也能检查与升级；升级后工作区与 git 不一致，可 `git checkout .` 恢复
  - 移除 `VERSION.txt` 与 `is_deployed` 判据；`/api/update/check` 返回 `source_worktree` 供前端提示
- 发行包不再内置 `config.json` 与 `VERSION.txt`
  - 配置是用户数据：缺省走 `DEFAULT_CONFIG`，面板保存时自动创建/写回
  - 版本以 `src/core/host.py` 的 `VERSION` 为唯一来源

## v0.15.3（2026-11-15）

### 修复

- `process` 型身份判定的修复未随 v0.15.2 发行包发布
  - v0.15.2 的发布提交漏带了 `src/core/state/manager.py`，发行包里的判定顺序仍是旧的
  - 受影响：仅 `process` 健康 + 仅 pidfile 的服务（如网络监控）在 v0.15.2 包上会显示 `stopped`
  - 本版补上：process 型优先走 `locate()`（句柄 / pidfile / 进程名），其余类型才要求 `identity.process`

## v0.15.2（2026-11-15）

### 修复

- `process` 型服务的身份判定被 v0.15.1 的改动截断
  - `_id_by_process` 在 process 分支前要求 `identity.process`，导致「process 型 + 仅 pidfile」（如网络监控）被判 `absent`
  - 现在 process 型优先走 `locate()`（句柄 / pidfile / 进程名），其余类型才要求 `identity.process`

## v0.15.1（2026-11-15）

### 修复

- `port` 型健康无法识别没有 pidfile 的实例
  - 此前 `port` 型只有 pidfile 一条路，`sd-server.exe` 这类第三方二进制永远判 `unknown`
  - 现在 `port` / `http` 型都允许 `identity.process` 唯一匹配，且要求与端口属主一致
  - 属主不符判 `foreign`（`process-port-mismatch`），不冒充本服务
  - `tests/test_identity.py` 新增 3 例：属主一致 → mine、属主不符 → foreign、无 identity → unknown

## v0.15.0（2026-11-15）

### 新增功能

- 局域网访问：新增 `cors_allow` 白名单
  - `config.json` 新增 `cors_allow`，列出允许的跨源来源（主机名 / 精确 IP / CIDR 网段），默认空 = 只本机
  - 配合 `host: 0.0.0.0`，可在局域网内打开面板并正常操作（此前写接口会被判为外站 `403`）
  - CORS 只约束浏览器跨源，不约束 curl / 脚本；真正的边界仍是网络层（防火墙 / Tailscale ACL）
  - `SECURITY.md`、`docs/design/cors.md`、README 的安全模型同步说明局域网部署取舍

## v0.14.1（2026-11-15）

### 文档

- README 按开源项目规范重写
  - 结构：特性 → 系统要求 → 安装（发行包 / 源码）→ 快速开始 → 使用 → 配置 → 目录结构 →
    升级与回滚 → 安全模型 → 开发
  - 安装以发行包为主入口（Releases 下载 → 解压 → 右键 `run\start.ps1` 使用 PowerShell 运行），
    源码运行单独成节
  - 新增系统要求表、升级与回滚、安全模型三节；「便携包（裁切打包）」不再出现在用户章节
- `docs/guide/usage.md` 的「便携包（裁切打包）」同步改称「发行包（下载即用）」

## v0.14.0（2026-11-15）

### 修复

- 前端硬编码接口地址，改端口的实例会连到别的宿主
  - `web/app.js` 的 `API_BASE` 改为同源相对路径
  - `src/svc.ps1` 改为从 `config.json` 读 host/port
- PowerShell 脚本的 `$home` 与内置只读变量 `$HOME` 冲突（变量名大小写不敏感）
  - 统一改名 `$dataRoot`（ctl.ps1 / svc.ps1 / install_autostart.ps1）
- 开关开机自启后提示“设置失败”，实际已生效
  - 成功回调里调了不存在的 `refresh()`（应调 `poll`），抛 ReferenceError 被 catch 捕获后回滚了开关
- 源码工作区点「检查更新」每次都报 502（拉 GitHub 的 `latest.json` 必然 404）
  - `/api/update/check` 改为先判断部署标记，源码工作区直接返回「请用 git pull」

### 新增功能

- 便携包（裁切打包，不内置 Python）
  - `tools/build-portable.ps1` 生成 `dist/my-service-<版本>.zip`（约 0.5 MB）
  - 结构：**包根 = 仓库结构**（`src/` `web/` `run/` `docs/guide/`）+ `reg/`（注册条）+ `data/`（logs/bak/update）+ `VERSION.txt`
  - 入口就是 `run/start.ps1` / `run/stop.ps1`（系统 Python 3.10+）；`-WithRuntime` 可选内嵌解释器
- 顶部栏提供开机自启开关与「检查更新」按钮
  - 从设置弹窗移出：自启是高频开关，更新要能一眼看到有没有新版本
  - 自启开关按 `/api/host` 的 `autostart` 渲染；计划任务模式只提示、不接管
  - 更新按钮两态：检查到新版本后转为「⬆ 升级到 vX」，再点即下载并自动重启生效
- `examples/demo`：可运行的示例环境
  - 一键复制源码到临时目录、用独立端口起实例，含 6 条示例注册条（3 服务 + 3 任务）
- README 增加控制台截图
  - `docs/guide/images/panel.png`，取自 demo 环境

### 文档

- 按读者分工：guide 讲怎么用，design 讲怎么实现
  - 新增 `docs/design/api.md`：完整 REST API 清单、错误格式、`?via=cli` 来源标记
  - `docs/guide/usage.md` 的 16 行接口表收敛为「常用接口」5 行，其余指向 design/api.md
- 去掉「没有 X」这类读者看不出意义的表述（如「没有 `app/` 层」），改为直接描述现状；
  同时删除「不再使用 reg/_archive/」等只剩内部历史价值的句子
- 修正过期内容：`docs/design/cron.md` 的 `reg/cron/*.json` → `reg/tasks/*.json`；
  `architecture.md`、`registry.md` 指向已删除 plan/ 的死链改为指向 CHANGELOG
- README 面板截图重截：顶部栏新增自启开关与更新按钮，画面改到内容底部（1360×1180 → 1360×693）

### 重构

- 代码根与数据根（home）分离
  - `src/server.py` 新增 `_resolve_home`：`MY_SERVICE_HOME` → 否则 = 代码根（包根与代码根同级）
  - `src/svc.ps1` / `run/install_autostart.ps1` 同步；解释器优先包内 `runtime/python.exe`（`-WithRuntime` 时）
  - 面板快捷方式统一放 `run/Dashboard.url`（每台机器生成，升级替换 `run/` 后自动重建）
- 运行时数据统一收进 `data/`（本项目约定：`logs/`、`bak/` 都不再位于顶层）
  - 日志落 `data/logs/`、备份落 `data/bak/`、升级暂存 `data/update/`；`.gitignore` 一行 `data/` 收口
- 便携包结构改为「包根 = 仓库结构」，删除 `app/` 层
  - `tools/build-portable.ps1` 直接组装 `src/ web/ run/` + 三个文档，数据骨架只建 `data/logs`、`data/bak`、`reg/services`、`reg/tasks`
  - 文档只带用户向的 `docs/guide/`（含 `images/panel.png`），架构/设计/排坑留在仓库
  - 升级按清单替换 `src/ web/ run/ docs/` + 三个文档：面板点更新 → 前端自动重启 → 旧宿主派生 `service_cli.py apply-update-and-start`，等旧进程退出后替换代码再起新宿主，旧代码留 `data/bak/code-prev/` 可回滚
  - 「是否支持一键升级」改用包根 `VERSION.txt` 判据（不再依赖 `runtime/`），源码工作区一律拒绝
- 开机自启改用启动文件夹 `.lnk` 快捷方式（取代 `vbs`）
  - 目标 `pythonw.exe` + `src/server.py`，pythonw 无控制台 → 登录无窗口；少一层 `wscript`，右键属性即可查看
  - `.lnk` 用系统自带 `powershell.exe` + `WScript.Shell` 生成（只在安装时用），面板开关与 `run/install_autostart.ps1` 行为一致，并清理历史 vbs/cmd

### 工程

- 发版改走流水线 `.github/workflows/release.yml`，本地不再手工打 tag
  - 触发：Actions 页面填版本号，或 `gh workflow run release.yml -f version=X.Y.Z`
  - tag 与 Release 只在检查全绿、便携包构建成功之后由流水线生成；任一步失败自动删 tag 与 Release
  - 顺序：preflight（版本一致 / CHANGELOG 段落 / 本地与远端无重名 tag）→ `ci.yml` 全量检查 →
    windows 打包并校验 `latest.json` → 草稿 Release 上传资产 → 资产与发布后自检 → 转正为 Latest
  - 发布说明直接取 `CHANGELOG.md` 该版本段落；便携包与 `latest.json` 作为 Release 资产上传，
    面板「检查更新」读到的就是这份
- `ci.yml` 支持 `workflow_call`，发版流水线与 PR 检查共用同一套命令，避免两处判据漂移

## v0.13.0（2026-11-14）

### 结构

- 前端 `src/web/` 迁至顶层 `web/`（与 `src/` 平级），`src/core/api.py` 的 `WEB_DIR` 相应上提一级
- README 目录树同步
- 新增 `AGENTS.md`（项目规则入口）：框架边界、运行态约定、按需读取表与改动流程
- 公开发布准备：README 重写、LICENSE（MIT）、CONTRIBUTING（含脱敏清单）、SECURITY、`tools/check_privacy.ps1`

### 编排模型（注册条重构）

- 注册条改为六组：`start / stop / health / identity / runtime / schedule`
- 目录：`reg/services/` + `reg/tasks/`，删除 `reg/cron/`、`reg/_archive/`
- 三种 mode：`managed / on_demand / task`；点火脚本用 `start.detach`（替代 `detached`）
- 健康只留 `http / port / process`；去掉 `none`
- 去掉 `conflict` 状态：端口被占、自报不符统一 `stopped` + `error`
- 运行态并入注册条 `runtime`，删除 `data/service_runtime.json`、`cron_runtime.json`、`service_order.json`、`cron_order.json`
- 回收仅 `on_demand`：`last_active` 优先，否则 `max(started_at, adopted_at)`
- `state/` 合并为 `manager.py` + `monitor.py`；`audit-prune` 并入 monitor
- detached 型入口脚本的标准输出纳入登记簿，静默失败可见

### 进程定位与状态

- 新增 Windows 进程信息：端口→PID（`GetExtendedTcpTable`）、进程创建时间、进程名/路径枚举
- `locate()` 回退链：句柄 → 框架 pidfile → 服务 pidfile → 端口 → 进程名
- 状态对象 9 字段：`status / pid / pid_source / started_at / started_source / first_seen_at / last_active / confidence / error`
- 停止外部实例：按定位到的 PID 树杀

### 节奏与设置

- monitor：面板活跃 1s / 空闲 10s；写操作后立即唤醒
- 面板心跳：`/api/panel/ping`（脚本调用不算活跃）
- scheduler：默认 10s，可配置；`interval.every_sec` 下限 10 秒
- 新增 `GET/POST /api/settings`（monitor 间隔、scheduler tick，写回 `config.json`）
- 备份保留策略：`bak/` 只留最近 N 份（`backup.keep`，默认 7，0 = 不自动清理）；设置弹窗可改，保存后立即收敛一次
- 前端轮询：可见 1s / 隐藏 10s

### 面板

- 「事件」按钮恢复独立，不再与设置面板联动
- 移除服务与定时任务的按路径关联：删除 `core/linkage.py` 与 snapshot 的 `cron_names` 字段
- 设置弹窗新增「事件保留天数」与「立即备份」（`reg/` + `config.json` + `adapters/` → `bak/`，不进 git）
- 服务列表改为三块分组 + 两行卡片布局，分组与卡片均可拖拽排序

### 修复

- 定时任务主行开关拨动后不生效

## v0.12.0（2026-10-08）

框架学会回答"**这个端口上跑的是不是我的服务**"。此前它只会问"端口有人应答吗"，
由此派生一类误判——把别人的应用认成自家服务（面板显示运行中、点开跳进陌生页面），
以及认不出宿主重启后的自家孤儿（既停不掉也回收不了）。

### 实例身份判定（新增 `identify()`）

- 四态判定：`mine`（确认是本服务，可接管/回收/停止）/ `foreign`（确认是别人的程序）/
  `unknown`（有应答但认不出，保守不冒充）/ `absent`（端口无监听）
- 判据按**信息产生时机**分层回退，全部零额外进程：
  `own-handle`（本进程拉起的句柄）→ `own-pidfile`（框架 pid 凭证）→
  `health-service`（服务 `/health` 自报的 `service` 字段）→
  `declared-pidfile`（注册条声明的 pidfile，商业软件兜底）
- **凭证类判据必须与"端口在监听"交叉验证**：pid 会被系统回收，残留凭证里的 pid
  可能已被判给别的进程，只看"pid 存活"会把别人的进程认成自家实例

### 启动接管（新增 `reconcile()`）

- 宿主重启后句柄表为空，此前只能靠端口探测（答不出身份），于是把孤儿一律 adopt
  成"外部实例"归位 running——既停不掉也回收不了，线上一天刷 3400+ 条登记簿
- 现按身份收敛：`mine` 归位、`foreign` 标 conflict、`unknown` 记事件不冒充、
  `absent` 清理过期 pid 凭证

### 新增面板状态

- `conflict`（端口被占，橙色）：与 `failed`（我启动失败）区分——处置方式不同
- `unknown`（身份不确认，黄色）：保守呈现，绝不显示成"运行中"

### 修复

- **停止服务闪黑窗**（v0.11 遗留）：`_taskkill_tree` / `pid_alive` / `wait_gone`
  三处 `subprocess.run` 漏传 `CREATE_NO_WINDOW`，注释声称"黑窗不打扰桌面"但实际
  都在弹窗；统一收敛到 `_run_quiet()` 入口，杜绝再漏
- **`opening.html` 对任何 HTTP 应答都跳转**：`fetch(mode:"no-cors")` 一 resolve
  就 `location.replace`——端口上是别人的程序也会照跳。现改为先经宿主身份确认
- **`host_start` 横幅恒为"定时任务: (无)"**：调度器只在自己循环里 `scan()`，取快照
  时还没扫过；宿主启动前补一次 `scan()`
- **端口冲突检查对 http 型全空转**：只认 `health.type=="port"` 的字段，而实际服务
  全是 `http`（端口写在 url 里），等于一个都没检查到；现从 url 解析
- **`quick_alive` 对 `none` 型恒返 True**：导致该类服务"启动即宣称健康就绪"，
  实际未做任何检查
- **`quick_alive` 对 port 型写死 IPv4**：只探 `127.0.0.1`，漏掉只监听 `::` 的服务
- **`your-service` 注册条未声明 `stop_script`**：自家有 `run/stop.ps1` 却没登记，
  成为孤儿后框架无手段停止 → 只能空转抹状态 → monitor 又归位 → 死循环

### 性能

- `pid_alive`：`tasklist`（约 300ms/次）→ `OpenProcess`（0.02ms），失败回退 tasklist
- `identify` 全链：2s → 52ms（端口关闭时先 socket 预筛，不走 `urlopen` 的约 2s 重试路径）

### 其他

- 登记簿新增动作：`identity_mismatch` / `identity_unknown` / `pidfile_stale`
- `stop()` 新增安全闸：端口被外人占用时**只清告警、绝不执行 `stop_script`**
  （否则会把停止指令打到陌生服务的接口上）
- `reg/your-service.json`、`reg/your-service.json`：端口迁至 33500 段
  （原 8xxx 段为本机最拥挤区间）；`reg/some-service.json` 补官方 pidfile 声明
- 测试：新增 `tests/test_identity.py` 14 例（四态判定、启动接管、停止安全闸、
  pid 回收防误判），全量 **120 例**通过

### 注意

- 本次改动在内存中生效，需**重启宿主一次**
- 两个自写服务的默认端口已调整到 33500 段；如你在别处硬编码过旧端口，请一并更新
  旧端口请一并更新。some-service 保持 8090

## v0.11.0（2026-09-24）

目录结构对齐项目库 README 三档模型，并把「声明 / 数据 / 记录」彻底分层：
**升完整档**（run/src/data/logs）+ 注册条瘦身（声明与运行态分离）。

### 组织模式（entry → run）

- `entry\` → **`run\`**（人的操作入口）。顺带解掉与注册条字段 `entry`（启动脚本）的撞名；
  代码/脚本/README/启动文件夹 cmd 的引用一并更新（**自启需重装一次**以刷新启动 cmd）
- `logs\` 与 `data\` 分离（判据：**删掉它，下次运行行为会变吗？** 会 → `data\`，不会 → `logs\`）
  - `logs\` 保留记录：`host.jsonl`（登记簿）、`host.err.log`
  - `data\` 承载数据：`host.pid`、`pids\`、`cron_state.json`、`cron_order.json`、
    `service_order.json`、`service_runtime.json`、`cron_runtime.json`
- `src\server.py`：`_setup_host_logging` 改为返回 `(logs_dir, data_dir)` 并分别注入
  audit（logs）/ runner·Manager·Scheduler（data）；`HostController` 同步改持两目录
- `src\core\runner.py`：`_RUNTIME_DIR` 语义改指 `data\`；`pidfile_path` 默认落 `data\pids\`
- `src\ctl.ps1`：建 `data\`、`host.pid` 读写改指 `data\`

### 注册条瘦身（声明 / 运行态分离）

背景：面板/宿主对注册条的写入是「读原文 → 改一个字段 → 全量 dump」，点一下自启开关
就会产生注册条 diff，与「注册条是配置、该进版本控制」的定位冲突。

- `reg\*.json` 只保留**声明**（path/entry/args/cwd/env/mode/health/url/display/
  description/stop_script/pidfile）；`enabled`/`autostart`/`idle_timeout_sec` 三个
  **运行态**字段迁到 `data\service_runtime.json`（不入库）
- 定时任务同构：`reg\cron\*.json` 只保留声明 + `schedule`（作「账本缺失时的默认计划」）；
  `enabled`/`timeout_sec`/`catch_up` 迁到 `data\cron_runtime.json`
- 读取时合并（`Spec.RUNTIME_KEYS` / `CronSpec.RUNTIME_KEYS`）：**账本优先**，注册条里的
  同名键只作**未迁移旧数据**的回落
- 面板四类写入（服务自启开关、空闲回收时长、任务时刻/开关/超时/补跑）只写账本；
  参数校验与「写后重解析 + 失败回滚」安全网保留，回滚对象改为账本
- 注销注册条时同步清理账本条目；注册条被删/停用的语义不变
- 面板操作实测：`reg\` 6 个注册条字节零改动（改造前那些 diff 噪音不复存在）

### 其他

- `.gitignore`：新增 `data/`、补 `*.tmp`（原子写中断残留可能被误跟踪）
- `README`：结构图重写（run/data/logs 分列）+ 全部路径引用同步
- `reg\your-service.json`：`entry` 由 `server.py` 改 `src/server.py`（随被签服务结构改造）
- 测试：旧用例由「断言注册条被写回」改为「注册条字节不变 + 账本更新」，新增分离语义用例
  10 个（账本优先 / 回落 / 重启保持 / 旧数据兼容 / 注销清账本），共 **106 个用例全绿**

### 注意

- **升级动作**：需**重启宿主一次**（改动在内存中生效），并**重装一次自启**
  （`run\install_autostart.ps1`，刷新启动文件夹 cmd 里写死的 `entry\` 路径）
- 运行态不入库的取舍：清 `data\` 或换机器后回落默认值——服务侧 `enabled=true`、
  `autostart=false`、`idle_timeout_sec=600`；任务侧回落到注册条里保留的 `schedule` 默认计划
  （这正是 cron 注册条不把 `schedule` 一并迁走的原因：清了账本也不至于让任务哑掉）

## v0.10.1（2026-09-08）

修 v0.10.0 自己带进去的一处小 bug。

- 服务设置面板里「关联的定时任务」的悬停提示取错字段名（用了 `schedule_desc`，
  快照里的字段其实叫 `schedule`）-> 提示会显示"描述 / undefined"；已改用 `schedule`
- 顺带修正 `reg\cron\your-service-snapshot.json` 的 description 里过时的
  "每天 23:00"（实际时刻是 23:30）

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
- `is_under` 按路径分量比较，`C:/path/to/svc/ab` 不会被误判为在 `C:/path/to/svc/a` 之下
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
  → 接入要求变硬：常驻服务必须自写文件日志且自轮转（规范与 python 片段见项目库 README「日志合规」）
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
- detached 软件的本体日志留在程序自家（某商业软件真日志 = `%LOCALAPPDATA%\<vendor>\<app>.log`，38 MB 无轮转），框架不代理读取
- 官方 `start.ps1` 将本体输出重定向到 TEMP 随机文件后删除，那份输出注定丢失

---

## v0.1（2026-08-30）

首个正式版本。本机服务管理框架（早期内部版本的整体重写版），纯 Python 标准库 + 原生 JS 零依赖，`127.0.0.1:1236`。

### 框架内核
- 四模块分层：`scanner`（注册中心扫描）/ `runner`（进程原子操作）/ `state`（状态机 Manager）/ `api`（REST + 静态托管）
- 状态机 `stopped → starting → running → stopping → failed`，monitor 线程每秒以健康探测校正并**强制超时收敛**（根治早期版本 `starting` 永久残留类问题）
- 两种托管模式：`managed`（框架 Popen 本体 + `taskkill /T` 树杀）/ `detached`（点火器型：官方启停脚本，死活只信健康探测）
- **防双开**（`external_alive` 统一判定）：port/http 走健康探测、process 走 pidfile 存活判据；冷启动检测到已有实例即拒绝重复拉起；自启遇已在运行则**跳过并归位 running**；detached 假失败（实例活着但超时）自动自愈
- **路径安全**：注册条 `path` 钉死服务真实家（cwd 敏感软件防漂移空库——某商业软件实弹教训）；`adapters\<svc>\` 可选转接层（相对路径优先命中，官方脚本回退）
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
- `reg\some-service.json`：某网关（8090），detached 直指官方脚本零副本；健康探测地址用 `localhost`（IPv6 监听面教训）

---
生成自 v0.1 前的 17 次提交（`779f6d6` → `13c2f33`）。