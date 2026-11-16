# my-service

[![release](https://img.shields.io/github/v/release/keeyangyy/my-service)](https://github.com/keeyangyy/my-service/releases)
[![license](https://img.shields.io/github/license/keeyangyy/my-service)](LICENSE)
[![python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)

**面向 AI 时代的本地控制台**：把你亲手写的那些小服务，收进一处统一管理。

AI 让「写一个服务」的门槛低了很多，本机上的脚本只会越来越多——但怎么起、怎么停、活着没有、开机要不要
自启、到点执行、出错去哪看，每个服务都得重来一遍。my-service 把这一层收进一个单页面板：
**一份 JSON 注册条描述一个服务**，启停、健康探测、自启、定时调度与事件审计全部由框架接管。

纯 Python 标准库实现，零第三方依赖；控制面默认只监听本机，改两行配置即可开放给局域网 / Tailscale 内的其他设备。

![控制台主面板](docs/guide/images/panel.png)

## 特性

- **声明式接入**：`reg/services/<name>.json` 描述路径、启停方式与健康判定，写入即生效，无需重启宿主。
- **三种托管形态**：`managed` 常驻托管、`on_demand` 按需启动并空闲回收、`task` 定时执行；
  只负责启停、不接管进程时用点火脚本形态 `start: {detach: true}`。
- **状态收敛**：探测结果按状态机收敛成明确状态，不停在中间态；端口被别的程序占用时保守告警，不误判成自己。
- **定时调度**：`reg/tasks/<name>.json` 声明 `daily` / `weekly` / `interval`，带超时保护与错过补跑。
- **事件审计**：启停与调度写入结构化日志 `data/logs/host.jsonl`，面板内直接检索。
- **对 AI / 脚本友好**：CLI + REST API + 结构化事件，agent 可直接读状态、触发任务、查事件，
  不必解析自然语言日志。
- **零第三方依赖**：只用 Python 标准库与原生 JavaScript，不引入运行时包管理器。

## 快速开始

要求：Windows 10 / 11、Python 3.10+（在 `PATH` 中）、PowerShell 7（推荐）。

1. 从 [Releases](https://github.com/keeyangyy/my-service/releases/latest) 下载 `my-service-<version>.zip`，
   解压到任意非受保护目录（例如 `C:/path/to/my-service`）。
2. 启动宿主：在 `run/` 里右键 `start.ps1` → 「使用 PowerShell 运行」，或终端执行：

   ```powershell
   pwsh -File run/start.ps1
   ```

3. 打开 <http://127.0.0.1:1236>。首次启动会在 `run/` 下生成 `Dashboard.url`，之后双击即可打开面板。

宿主生命周期与其他入口：

| 操作 | 命令 |
|:--|:--|
| 停止 / 重启宿主 | `pwsh -File run/stop.ps1` / `pwsh -File run/restart.ps1` |
| 安装 / 卸载开机自启 | `pwsh -File run/install_autostart.ps1` / `uninstall_autostart.ps1`（等价面板顶部「自启」开关） |
| 从源码运行 | `git clone … && cd my-service && pwsh -File ./run/start.ps1` |

源码工作区也能用面板的一键更新，但它会替换 `src/ web/ run/ docs/` 与三个文档，之后工作区与 git 不一致
（`git checkout .` 可还原）；想保留 git 历史时优先 `git pull`。

## 接入一个服务

接入前需要三样东西：**启动入口**、**停止脚本**、**活着怎么判断**。在 `reg/services/` 下写一份注册条：

```json
{
  "path": "C:/path/to/your-service",
  "display": "your-service",
  "description": "示例：带网页面板的本机服务",
  "mode": "on_demand",
  "url": "http://127.0.0.1:33510/",
  "start": { "entry": "src/server.py" },
  "stop": { "script": "run/stop.ps1" },
  "identity": {
    "http": "http://127.0.0.1:33510/health",
    "service_name": "your-service",
    "pidfile": "data/server.pid"
  },
  "settings": { "state": "active", "autostart": false, "idle_timeout_sec": 600 }
}
```

保存即生效，刷新面板就能看到。三个容易踩的点：

- `identity` 可同时写 `http` / `port` / `pidfile` / `process`，健康判定按 `http → port → process`
  取最靠前的一个；其余成员继续参与「端口上跑的是不是本服务」的身份判定；
- 相对 `start.entry` / `stop.script` 只在 `path` 下解析（0.19 起没有框架侧转接目录），脚本请放进服务自己家里；
- `task` 模式的 `state` 默认 `disabled`，注册完要手动启用。

完整字段表、常见提示与排查步骤见 **[注册一个服务](docs/guide/registration.md)**。

## 使用

界面按 `mode` 分三块，空组隐藏，组内可拖拽排序（顺序写回注册条 `settings.order`）：

| 区域 | 能做什么 |
|:--|:--|
| 顶部栏 | 开机自启、检查更新、编排器设置、事件登记簿、重启 / 停止宿主 |
| 服务卡片 | 看状态与端口、启动 / 停止、设置（空闲回收时长、自启）、查看事件；点击卡片打开服务页面 |
| 定时任务 | 启用开关、立即运行、设置（时刻 / 间隔 / 超时 / 补跑）、查看事件 |

命令行（服务名用 `ls` 查）：

```powershell
python src/service_cli.py ls                 # 服务列表与状态
python src/service_cli.py check              # 校验全部注册条（加 --json 输出结构化结果）
python src/service_cli.py svc-start <name>   # 启动 / 停止 / 重启指定服务
python src/service_cli.py svc-stop <name>
python src/service_cli.py svc-restart <name>
python src/service_cli.py crons              # 定时任务列表
python src/service_cli.py cron-run <name>    # 立即执行一次任务
python src/service_cli.py events [name] [n]  # 查看启停登记簿
python src/service_cli.py host-err [n]       # 查看宿主异常栈
```

REST API 完整清单、配置项细节与升级回滚说明见 **[使用指南](docs/guide/usage.md)**。

## 配置

`config.json` 是可选的：不存在时用内置默认，面板「设置」改完立即生效并自动写回。

| 字段 | 默认 | 说明 |
|:--|:--|:--|
| `server.host` / `server.port` | `127.0.0.1` / `1236` | 控制面监听地址与端口 |
| `cors.local_only` / `cors.allow` | `true` / `[]` | 跨源策略：默认只认本机来源，`allow` 补放行主机名 / IP / CIDR |
| `audit.retention_days` | `7` | 事件保留天数，`0` = 不清理 |
| `backup.keep` | `7` | `data/bak/` 保留份数，`0` = 不自动清理 |
| `update.mirrors` | 内置 4 个代理前缀 | 更新加速源，`[]` = 只用官方 GitHub |
| `advanced.reg_dir` | `reg` | 注册条根目录 |
| `advanced.monitor.*` / `advanced.scheduler.tick_sec` | `1` / `10` / `15` / `10` | 轮询间隔、面板活跃宽限、调度检查间隔 |

数据目录默认就是包根；设置环境变量 `MY_SERVICE_HOME` 可把 `data/` 与 `reg/` 指到别处。

**要从别的机器访问面板**：把 `server.host` 改成 `0.0.0.0`，并在 `cors.allow` 里列出来源
（如 `["my-laptop", "100.64.0.0/10"]`）。注意 CORS 只约束浏览器，不约束 curl / 脚本——真正的边界在网络层。

## 目录结构

```
my-service/
├── config.json                # 控制面与保留策略（可选，面板会写回）
├── reg/
│   ├── services/<name>.json   # 服务注册条（唯一发现源）
│   └── tasks/<name>.json      # 定时任务注册条
├── run/                       # 人工入口：start / stop / restart / 自启安装与卸载
├── data/                      # 运行时数据：logs / bak / update / pids / entry-out
├── src/
│   ├── server.py              # 装配入口（config → Scanner → Manager → Scheduler → HTTP）
│   ├── service_cli.py         # 唯一 CLI 实现（宿主生命周期 + 服务管理）
│   └── core/                  # api / state / scheduler / runner / scanner / audit / backup
├── web/                       # 面板静态资源
├── tools/                     # 便携包构建、隐私检查、更新包签名
├── examples/                  # 示例注册条与可运行 demo
├── tests/                     # 单元测试
└── docs/                      # guide 面向使用者，design 面向开发者
```

## 升级与回滚

面板顶部「⟳ 更新」先只读检查版本，确认后才下载。包体 sha256 与大小必须与清单一致，清单本身带
Ed25519 签名（客户端内置公钥验签），**缺失或验签失败一律拒绝**。

生效时替换 `src/`、`web/`、`run/`、`docs/` 与 `README.md`、`CHANGELOG.md`、`LICENSE` 后重启宿主；
`reg/`、`data/`、`config.json` 不受影响。失败会自动回滚，原因写 `data/update/last-error.json`，
上一版代码留在 `data/bak/code-prev/`。

## 安全模型

默认只绑定回环地址、只向本机来源网页授予跨源权限，不提供账户体系——前提是「本机进程本身即可调用接口」。
扩大监听范围时，真正的边界在网络层：建议用防火墙只放行 Tailscale 或局域网网段。

威胁模型与漏洞报告方式见 [SECURITY.md](SECURITY.md)。

## 开发

```powershell
python -m unittest discover -s tests              # 全量单元测试
python -m ruff check src tests                    # 静态检查
pwsh -File ./tools/check_privacy.ps1              # 发布前隐私检查（本机路径 / 凭据 / 私有名词）
pwsh -File ./examples/demo/start-demo.ps1         # 起一个隔离的 demo 实例（端口 1246，-Stop 清理）
```

代码约定与提交规范见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 文档

| 文档 | 面向 | 内容 |
|:--|:--|:--|
| [使用指南](docs/guide/usage.md) | 使用者 | 四层入口、完整 REST 接口、配置项、升级回滚、卸载 |
| [注册一个服务](docs/guide/registration.md) | 使用者 | 三件套要求、字段速查、identity 写法、定时任务、排查 |
| [注册条规范](docs/design/config-schema.md) | 开发者 | 注册条字段的权威定义与迁移说明 |
| [架构](docs/architecture.md) | 开发者 | 状态机、身份判定、线程模型 |
| [设计文档](docs/design/) | 开发者 | api / cron / cors / logging / on-demand / panel / update |
| [更新日志](CHANGELOG.md) | — | 版本变更 |

## 许可证

[MIT](LICENSE)
