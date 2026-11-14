# 日志边界与登记簿

> 状态：**已实现（后端）**（2026-11-14 重构）

## 原则

**编排器只记自己做了什么，不记服务内部发生了什么。**

- 服务加不加进框架，它的日志形态一字不变；
- 一个文件只有一个作者；
- 健康探测不写访问日志。

| 流 | 文件 | 作者 | 内容 |
|:--|:--|:--|:--|
| 控制面 | `my-service/logs/host.jsonl` | 框架 | 状态变化事件，一行一条 |
| 宿主自身 | `my-service/logs/host.err.log` | 框架 | 未捕获异常栈 |
| 数据面 | `<服务 path>/logs/service.log` | 服务本人 | 业务日志，格式与轮转自定 |

## 入口输出（entry-out）

只对 `start.detach: true`（点火脚本）生效：

```
data/entry-out/<服务名>.out
```

- 点火脚本拉起真进程后自己退场，它的输出是“起没起来”的唯一说明；
- 启动失败时框架取尾部写进登记簿 `note`；
- 常驻服务的 stdout/stderr 仍然 DEVNULL；
- 内容不是服务日志，不对外展示全文。

## 登记簿事件

固定 7 字段：`ts` / `svc`（宿主事件为 null）/ `act` / `ok` / `dur_ms` / `pid` / `note`。

| `act` | 何时出现 |
|:--|:--|
| `host_start` / `host_stop` | 宿主起来 / 收工 |
| `start` | starting 收敛为终态：成功带耗时与 pid，失败 note 给原因 |
| `stop` | 一次停止完成 |
| `autostart_skip` | 自启时实例已在运行，跳过并归位 |
| `adopt_external` | 检测到外部/手动实例，归位 running |
| `health_flip` | running 期健康翻转（失联归位 / 假失败自愈） |
| `identity_mismatch` | 端口被别的程序占用，或自报 service 不符（状态记 `stopped` + error） |
| `idle_reclaim` | on_demand 超时回收 |
| `crash` | 托管进程意外退出（含 rc） |
| `spawn_fail` | 进程没能起来 |
| `reject_double` | 拒绝重复拉起 |
| `spec_invalid` | 注册条无效或被移除 |
| `legacy_reg` | 读到旧版注册条字段（兼容层告警） |
| `cron_run` | 定时任务执行一轮 |
| `cron_skip` | 错过且不补跑，顺延 |

`restart` 不单独成事件，表现为 `stop → start` 两条。动作枚举以 `src/core/audit.py` 的 `ACTS` 为准，新增须同步前端 `ACT_MAP`。

## 清空与自动清理

- 面板：卡片「事件」只清该服务；全局登记簿清整本（均需确认）；
- 自动清理：`config.json` 的 `audit_retention_days`（默认 7 天，`0` = 永不），
  由 monitor 每小时顺带执行（不再单独起线程）。

## 查看

- 面板「▤ 登记簿」/ 卡片「事件」
- `./src/svc.ps1 events [name] [n]`
- `GET /api/audit?n=&svc=&fail=1`

## 明确不做

- 商业软件本体日志留在它自己的目录（某商业软件的 `<app>.log`），框架不代管、不代理读取；
- 服务裸 `print` 不落文件就是没有日志，框架不兜底；
- 框架不写“服务业务日志”，历史上前车之鉴是混码与归属不清。
