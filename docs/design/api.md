# 控制面 REST API

> 状态：**已实现**（2026-11-14 重构）｜ 实现：`src/core/api/__init__.py`
> 面向：开发者 / 脚本调用 —— 日常操作看 [使用指南](../guide/usage.md)

## 约定

- 只监听 `127.0.0.1`，无认证；任何本机进程都能直接调用，这是刻意的取舍（见 [SECURITY](../../SECURITY.md)）。
- 响应统一 JSON；失败形如 `{"ok": false, "error": "原因"}`，HTTP 状态码同时反映类别（400 参数 / 404 未知对象 / 500 内部错误）。
- 启停类接口带 `?via=cli` 时，登记簿会把它标为 CLI 发起，便于区分面板点击与脚本调用。
- 跨源行为见 [cors.md](cors.md)：默认只放行本机来源的网页，跨源写操作一律拒绝。

## 接口

| 方法 | 路径 | 说明 |
|:--|:--|:--|
| GET | `/health` | 宿主存活 |
| GET | `/api/host` | 宿主 pid / uptime / version / autostart |
| GET | `/api/services` | 服务列表（含状态、端口、关联任务） |
| GET | `/api/services/{name}` | 单服务 |
| GET | `/api/audit?n=&svc=&fail=1` | 启停登记簿 |
| GET | `/api/host-err?n=` | 宿主异常栈 |
| GET | `/api/crons` | 定时任务列表 |
| GET | `/api/settings` | 编排器全局设置 |
| GET | `/api/update/check` | 检查新版本（仅打包部署可用） |
| POST | `/api/services/order` | `{"names":[...]}` 拖拽排序 |
| POST | `/api/services/{svc}/start\|stop\|restart[?via=cli]` | 启停 |
| POST | `/api/services/{svc}/config` | `{"idle_timeout_sec": N}`（仅 on_demand） |
| POST | `/api/crons/order` | `{"names":[...]}` 拖拽排序 |
| POST | `/api/crons/{name}/run[?via=cli]` | 手动触发 |
| POST | `/api/crons/{name}/config` | `enabled` / `catch_up` / `timeout_sec` / `schedule` 任意组合 |
| POST | `/api/config/{svc}` | `{"autostart": bool}` |
| POST | `/api/settings` | `monitor` / `scheduler` / `backup` 任意组合，写回 `config.json` |
| POST | `/api/host/autostart` | `{"enabled": bool}` 开关开机自启 |
| POST | `/api/host/stop\|restart` | 宿主自身生命周期 |
| POST | `/api/audit/clear` | `{"svc":"名"}` 清该名事件 / `{}` 清全部 |
| POST | `/api/backup` | 立即备份 `reg/` + `config.json` + `adapters/` |
| POST | `/api/update/apply` | `{"version","url","sha256"}` 下载暂存，随后自动重启生效 |

## 与内部对象的关系

- 服务与任务都以注册条为唯一发现源，API 只做读写，不缓存名单：见 [registry.md](registry.md)、[cron.md](cron.md)。
- 状态机与健康判定在 [architecture.md](../architecture.md)，API 只是把状态透出。
- 宿主自身不进服务列表，所以「重启/停止宿主」走 `/api/host/*` 专属通道，避免自举。
