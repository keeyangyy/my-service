"""my-service · core/api.py

HTTP 层：REST API + web/ 静态托管。绑定 127.0.0.1，零第三方依赖。
"""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from .. import audit, backup
from .cors import is_local_origin, is_write_path, origin_host, origin_of
from .static import load as load_static

# 兼容旧私有名（原 core/api.py 的模块级纯函数，保持外部引用不破）
_origin_of = origin_of
_origin_host = origin_host
_is_local_origin = is_local_origin
_is_write_path = is_write_path


class _Handler(BaseHTTPRequestHandler):
    # 由 make_handler 在子类上绑定（每个实例一套，避免多实例相互串味）
    manager = None
    host = None
    scheduler = None
    settings_path = None
    backup_settings = None
    cors_local_only: bool = True
    server_version = "my-service"
    protocol_version = "HTTP/1.1"

    # ---------------------------------------------------------- 工具
    def _acao(self) -> tuple[str, bool]:
        """本请求的 CORS 处置：(回显的 Origin 或 '', 是否本地来源)。

        未带 Origin（同源 / curl / 非浏览器）→ 不加头，行为与历史完全一致。
        开关关闭 → 一律不加头，整体退回无 CORS 的历史语义。
        """
        if not self.cors_local_only:
            return "", True
        origin = _origin_of(self.headers)
        if origin is None:
            return "", True
        return (origin, True) if _is_local_origin(origin) else ("", False)

    def _denied(self, path: str) -> bool:
        """外站是否应被拒绝：仅拦写接口，且仅在开关开启时。"""
        if not self.cors_local_only:
            return False
        origin = _origin_of(self.headers)
        if origin is None or _is_local_origin(origin):
            return False
        return _is_write_path(path)

    def _json(self, code: int, payload: dict) -> None:
        origin, local = self._acao()
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        elif not local:
            # 外站：明确不加 ACAO，浏览器自会拦下响应
            pass
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _static(self, rel: str) -> None:
        loaded = load_static(rel)
        if loaded is None:
            return self._json(404, {"ok": False, "error": "not found"})
        body, mime = loaded
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):  # noqa: A003 - 静默访问日志
        pass

    def _backup_keep(self) -> int:
        """当前备份保留份数（0 = 不自动清理），非法值回落到默认 7。"""
        try:
            return int((self.backup_settings or {}).get("keep",
                                                   backup.DEFAULT_KEEP))
        except (TypeError, ValueError):
            return backup.DEFAULT_KEEP

    # ---------------------------------------------------------- 预检
    def do_OPTIONS(self):  # noqa: N802
        """CORS 预检：只有本机来源的页面才拿到跨源许可，其余明确拒绝。

        外站拿到 403 且无 ACAO 头 → 浏览器不会发主请求，写操作无从触发。
        """
        origin, local = self._acao()
        if not local:
            return self._json(403, {"ok": False, "error": "cross-origin denied"})
        self.send_response(204)
        self.send_header("Allow", "GET, POST, OPTIONS")
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header(
                "Access-Control-Allow-Headers", "Content-Type, X-Requested-With")
            self.send_header("Access-Control-Max-Age", "600")
        self.send_header("Content-Length", "0")
        self.end_headers()

    # ---------------------------------------------------------- GET
    # ---------------------------------------------------------------- GET
    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        routes = {
            "/health": self._get_health,
            "/api/panel/ping": self._get_panel_ping,
            "/api/settings": self._get_settings,
            "/api/backups": self._get_backups,
            "/api/host": self._get_host,
            "/": self._get_index,
            "/index.html": self._get_index,
            "/api/audit": self._get_audit,
            "/api/host-err": self._get_host_err,
            "/api/crons": self._get_crons,
        }
        handler = routes.get(path)
        if handler is not None:
            return handler(parsed)
        if path.startswith("/api/services"):
            return self._get_services(path)
        rest = path.lstrip("/")
        if rest and not rest.startswith("api/"):
            return self._static(rest)
        return self._json(404, {"ok": False, "error": "not found"})

    def _get_health(self, parsed) -> None:  # noqa: ANN001
        return self._json(200, {"ok": True})

    def _get_panel_ping(self, parsed) -> None:  # noqa: ANN001
        note = getattr(self.manager, "note_panel", None)
        if callable(note):
            note()
        return self._json(200, {"ok": True})

    def _get_settings(self, parsed) -> None:  # noqa: ANN001
        return self._json(200, {
            "ok": True,
            "monitor": dict(getattr(self.manager, "settings", {}) or {}),
            "scheduler": {"tick_sec": getattr(self.scheduler, "tick_sec", 10.0)},
            "backup": {"keep": self._backup_keep()},
        })

    def _get_backups(self, parsed) -> None:  # noqa: ANN001
        items = (backup.list_backups(Path(self.settings_path).parent)
                 if self.settings_path else [])
        return self._json(200, {"ok": True, "backups": items})

    def _get_host(self, parsed) -> None:  # noqa: ANN001
        if self.host is None:
            return self._json(200, {"ok": True})
        return self._json(200, self.host.meta())

    def _get_index(self, parsed) -> None:  # noqa: ANN001
        return self._static("index.html")

    def _get_audit(self, parsed) -> None:  # noqa: ANN001
        """控制面登记簿（logs/host.jsonl）——不含任何服务业务输出。"""
        qs = parse_qs(parsed.query)
        n = _num(qs, "n", 200)
        svc = (qs.get("svc") or [None])[0] or None
        only_fail = (qs.get("fail") or ["0"])[0].lower() in ("1", "true", "yes")
        return self._json(200, {
            "ok": True,
            "source": str(audit.current_path() or ""),
            "events": audit.tail(n, svc, only_fail),
        })

    def _get_host_err(self, parsed) -> None:  # noqa: ANN001
        """宿主自身异常栈（logs/host.err.log）。"""
        qs = parse_qs(parsed.query)
        n = _num(qs, "n", 200)
        return self._json(200, _tail_file(
            _host_logs_dir(self.manager, self.host) / "host.err.log", "(host-err)", n))

    def _get_crons(self, parsed) -> None:  # noqa: ANN001
        crons = self.scheduler.snapshot() if self.scheduler else []
        return self._json(200, {"crons": crons})

    def _get_services(self, path: str) -> None:
        parts = [p for p in path.split("/") if p]
        if len(parts) == 2:
            return self._json(200, {"services": self.manager.snapshot()})
        name = parts[2]
        svc = next((s for s in self.manager.snapshot() if s["name"] == name), None)
        if svc is None:
            return self._json(404, {"ok": False, "error": f"未知服务 {name}"})
        return self._json(200, svc)

    # ---------------------------------------------------------------- POST
    def do_POST(self):  # noqa: N802
        body = self._read_body()
        if body is None:
            return
        parsed = urlparse(self.path)
        parts = [p for p in unquote(parsed.path).split("/") if p]
        # 外站门禁：写接口统一在此拦截，避免逐条路由漏挂
        if self._denied(unquote(parsed.path)):
            return self._json(403, {"ok": False, "error": "cross-origin denied"})
        # 写操作后立即唤醒 monitor，不等空闲间隔
        wake = getattr(self.manager, "wake", None)
        if callable(wake):
            wake()
        matched = (
            self._post_backup(parts, body)
            or self._post_settings(parts, body)
            or self._post_service_order(parts, body)
            or self._post_service_config(parts, body)
            or self._post_service_action(parts, body, parsed)
            or self._post_host_action(parts)
            or self._post_audit_clear(parts, body)
            or self._post_crons_order(parts, body)
            or self._post_cron_run(parts, parsed)
            or self._post_cron_config(parts, body)
            or self._post_autostart(parts, body)
        )
        if not matched:
            self._json(404, {"ok": False, "error": "not found"})

    def _read_body(self) -> dict | None:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            self._json(400, {"ok": False, "error": "Content-Length 非法"})
            return None
        raw = self.rfile.read(length) if length > 0 else b""
        try:
            body = json.loads(raw.decode("utf-8")) if raw else {}
        except Exception:  # noqa: BLE001
            self._json(400, {"ok": False, "error": "请求体不是合法 JSON"})
            return None
        if not isinstance(body, dict):
            self._json(400, {"ok": False, "error": "请求体必须是 JSON 对象"})
            return None
        return body

    @staticmethod
    def _is(parts: list[str], *expect: str) -> bool:
        return len(parts) == len(expect) and all(
            a == b for a, b in zip(parts, expect, strict=True))

    def _post_backup(self, parts: list[str], body: dict) -> bool:
        """POST /api/backup —— 立即备份 reg/ + config.json + adapters/。"""
        if not self._is(parts, "api", "backup"):
            return False
        if not self.settings_path:
            self._json(501, {"ok": False, "error": "未配置备份根目录"})
            return True
        try:
            info = backup.create_backup(Path(self.settings_path).parent,
                                        keep=self._backup_keep())
        except OSError as exc:
            self._json(500, {"ok": False, "error": f"备份失败：{exc}"})
            return True
        self._json(200, {"ok": True, **info})
        return True

    def _post_settings(self, parts: list[str], body: dict) -> bool:
        """POST /api/settings {"monitor": {...}, "scheduler": {...}, "backup": {...}}"""
        if not self._is(parts, "api", "settings"):
            return False
        mon, sch, bkp = body.get("monitor"), body.get("scheduler"), body.get("backup")
        if not (isinstance(mon, dict) or isinstance(sch, dict) or isinstance(bkp, dict)):
            self._json(400, {"ok": False,
                             "error": "需要 monitor / scheduler / backup 对象"})
            return True
        if self._apply_monitor(mon) or self._apply_tick(sch) or self._apply_backup_keep(bkp):
            return True
        if self.settings_path and not self._persist_settings():
            return True
        self._json(200, {"ok": True})
        return True

    def _apply_monitor(self, mon: object) -> bool:
        """应用 monitor 各项；返回 True 表示已发出错误响应。"""
        if not isinstance(mon, dict):
            return False
        for key in ("fast_sec", "idle_sec", "panel_grace_sec", "audit_retention_days"):
            if key not in mon:
                continue
            try:
                val = float(mon[key])
            except (TypeError, ValueError):
                self._json(400, {"ok": False, "error": f"{key} 需为数字"})
                return True
            if key == "audit_retention_days":
                if not (0 <= val <= 3650):
                    self._json(400, {"ok": False,
                                     "error": "audit_retention_days 需在 0 ~ 3650 天之间"})
                    return True
                self.manager.settings[key] = val
            else:
                self.manager.settings[key] = max(0.2, val)
        return False

    def _apply_tick(self, sch: object) -> bool:
        if not (isinstance(sch, dict) and "tick_sec" in sch):
            return False
        try:
            self.scheduler.tick_sec = max(1.0, float(sch["tick_sec"]))
        except (TypeError, ValueError):
            self._json(400, {"ok": False, "error": "tick_sec 需为数字"})
            return True
        return False

    def _apply_backup_keep(self, bkp: object) -> bool:
        if not (isinstance(bkp, dict) and "keep" in bkp):
            return False
        try:
            keep = int(float(bkp["keep"]))
        except (TypeError, ValueError):
            self._json(400, {"ok": False, "error": "备份保留份数需为数字"})
            return True
        if not (0 <= keep <= 365):
            self._json(400, {"ok": False,
                             "error": "备份保留份数需在 0 ~ 365 之间（0 = 不自动清理）"})
            return True
        if self.backup_settings is not None:
            self.backup_settings["keep"] = keep
            # 立即按新策略收敛一次，保存后 bak/ 里马上只剩 keep 份
            if self.settings_path:
                backup.prune_backups(Path(self.settings_path).parent, keep)
        return False

    def _persist_settings(self) -> bool:
        """把当前生效值写回 config.json；失败时已发出 500，返回 False。"""
        try:
            sp = Path(self.settings_path)
            cfg = (json.loads(sp.read_text(encoding="utf-8-sig"))
                   if sp.is_file() else {})
            cfg["monitor"] = {
                **cfg.get("monitor", {}),
                **{k: self.manager.settings[k] for k in
                   ("fast_sec", "idle_sec", "panel_grace_sec", "audit_retention_days")},
            }
            cfg["scheduler"] = {
                **cfg.get("scheduler", {}),
                "tick_sec": getattr(self.scheduler, "tick_sec", 10.0),
            }
            cfg["backup"] = {**cfg.get("backup", {}), "keep": self._backup_keep()}
            sp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n",
                          encoding="utf-8")
        except OSError:
            self._json(500, {"ok": False, "error": "写 config.json 失败"})
            return False
        return True

    def _post_service_order(self, parts: list[str], body: dict) -> bool:
        """POST /api/services/order {"names": [...]} —— 面板拖拽排序。"""
        if not self._is(parts, "api", "services", "order"):
            return False
        names = body.get("names")
        if not isinstance(names, list):
            self._json(400, {"ok": False, "error": '需要数组字段 "names"'})
            return True
        ok, _code, msg = self.manager.set_order([str(n) for n in names])
        self._json(200 if ok else 500, {"ok": ok, "message": msg})
        return True

    def _post_service_config(self, parts: list[str], body: dict) -> bool:
        """POST /api/services/{name}/config（仅 on_demand 的 idle_timeout_sec）。"""
        if not (len(parts) == 4 and parts[0] == "api" and parts[1] == "services"
                and parts[3] == "config"):
            return False
        patch = {k: v for k, v in body.items() if k in ("idle_timeout_sec",)}
        if not patch:
            self._json(400, {"ok": False, "error": "至少提供 idle_timeout_sec"})
            return True
        ok, code, msg = self.manager.update_config(parts[2], patch)
        http_code = {"ok": 200, "not_found": 404, "invalid": 400, "io": 500}.get(code, 500)
        self._json(http_code, {"ok": ok, "message": msg, "error": None if ok else msg})
        return True

    def _post_service_action(self, parts: list[str], body: dict, parsed) -> bool:  # noqa: ANN001
        """POST /api/services/{name}/{start|stop|restart}[?via=cli]"""
        if not (len(parts) == 4 and parts[0] == "api" and parts[1] == "services"):
            return False
        name, action = parts[2], parts[3]
        if action not in ("start", "stop", "restart"):
            self._json(404, {"ok": False, "error": f"未知动作 {action}"})
            return True
        via = (parse_qs(parsed.query).get("via") or ["api"])[0]
        if action == "start":
            ok, msg = self.manager.start(name, via=via)
        elif action == "stop":
            ok, msg = self.manager.stop(name)
        else:
            ok, msg = self.manager.restart(name, via=via)
        status_now = next(
            (s["status"] for s in self.manager.snapshot() if s["name"] == name), None)
        self._json(202 if ok else 500,
                   {"ok": ok, "message": msg, "status": status_now})
        return True

    def _post_host_action(self, parts: list[str]) -> bool:
        """POST /api/host/{stop|restart} —— 宿主自身生命周期（专属通道）。"""
        if not (len(parts) == 3 and parts[0] == "api" and parts[1] == "host"):
            return False
        action = parts[2]
        if self.host is None:
            self._json(501, {"ok": False, "error": "宿主自管理不可用"})
            return True
        if action not in ("stop", "restart"):
            self._json(404, {"ok": False, "error": f"未知宿主动作 {action}"})
            return True
        self.host.schedule(action)   # 延迟执行，先让本响应发出
        self._json(202, {"ok": True, "action": action})
        return True

    def _post_audit_clear(self, parts: list[str], body: dict) -> bool:
        """POST /api/audit/clear {"svc": "name"} 或 {} 清空全部。"""
        if not self._is(parts, "api", "audit", "clear"):
            return False
        svc = body.get("svc")
        if svc is not None and not isinstance(svc, str):
            self._json(400, {"ok": False, "error": "svc 需为字符串或 null"})
            return True
        removed = audit.clear(svc or None)
        self._json(200, {"ok": True, "removed": removed})
        return True

    def _post_crons_order(self, parts: list[str], body: dict) -> bool:
        """POST /api/crons/order {"names": [...]} —— 面板拖拽排序。"""
        if not self._is(parts, "api", "crons", "order"):
            return False
        if self.scheduler is None:
            self._json(501, {"ok": False, "error": "调度器不可用"})
            return True
        names = body.get("names")
        if not isinstance(names, list):
            self._json(400, {"ok": False, "error": '需要数组字段 "names"'})
            return True
        ok, _code, msg = self.scheduler.set_order([str(n) for n in names])
        self._json(200 if ok else 500, {"ok": ok, "message": msg})
        return True

    def _post_cron_run(self, parts: list[str], parsed) -> bool:  # noqa: ANN001
        """POST /api/crons/{name}/run —— 手动立即触发定时任务。"""
        if not (len(parts) == 4 and parts[0] == "api" and parts[1] == "crons"
                and parts[3] == "run"):
            return False
        if self.scheduler is None:
            self._json(501, {"ok": False, "error": "调度器不可用"})
            return True
        via = (parse_qs(parsed.query).get("via") or ["manual"])[0]
        ok, code, msg = self.scheduler.run_now(parts[2], via=via)
        http_code = {"ok": 202, "not_found": 404, "invalid": 400,
                     "busy": 409, "disabled": 409}.get(code, 500)
        self._json(http_code, {"ok": ok, "message": msg})
        return True

    def _post_cron_config(self, parts: list[str], body: dict) -> bool:
        """POST /api/crons/{name}/config —— enabled / catch_up / timeout_sec / schedule。"""
        if not (len(parts) == 4 and parts[0] == "api" and parts[1] == "crons"
                and parts[3] == "config"):
            return False
        if self.scheduler is None:
            self._json(501, {"ok": False, "error": "调度器不可用"})
            return True
        patch = {k: v for k, v in body.items()
                 if k in ("enabled", "catch_up", "timeout_sec", "schedule")}
        if not patch:
            self._json(400, {"ok": False, "error":
                             "至少提供 enabled / catch_up / timeout_sec / schedule 之一"})
            return True
        for flag in ("enabled", "catch_up"):
            if flag in patch and not isinstance(patch[flag], bool):
                self._json(400, {"ok": False, "error": f"{flag} 需为布尔"})
                return True
        ok, code, msg = self.scheduler.update_config(parts[2], patch)
        http_code = {"ok": 200, "not_found": 404, "invalid": 400, "io": 500}.get(code, 500)
        self._json(http_code, {"ok": ok, "message": msg, "error": None if ok else msg})
        return True

    def _post_autostart(self, parts: list[str], body: dict) -> bool:
        """POST /api/config/{name} {"autostart": bool}"""
        if not self._is(parts, "api", "config") and not (
                len(parts) == 3 and parts[0] == "api" and parts[1] == "config"):
            return False
        value = body.get("autostart")
        if not isinstance(value, bool):
            self._json(400, {"ok": False, "error": '需要布尔字段 "autostart"'})
            return True
        ok, msg = self.manager.toggle_autostart(parts[2], value)
        self._json(200 if ok else 500, {"ok": ok, "error": None if ok else msg})
        return True


def make_handler(manager, host=None, scheduler=None,
                 cors_local_only: bool = True,
                 settings_path=None, backup_settings=None):  # noqa: ANN001
    """绑定依赖后返回 Handler 子类（依赖走类属性，方法体不依赖闭包）。"""
    return type("Handler", (_Handler,), {
        "manager": manager,
        "host": host,
        "scheduler": scheduler,
        "settings_path": settings_path,
        "backup_settings": backup_settings,
        "cors_local_only": cors_local_only,
    })


def _num(qs: dict, key: str, default: int) -> int:
    try:
        return max(1, min(1000, int(qs.get(key, [str(default)])[0])))
    except (TypeError, ValueError):
        return default


def _host_logs_dir(manager, host) -> Path:  # noqa: ANN001
    """宿主记录目录：<my-service>/logs —— 登记簿 host.jsonl / 异常栈 host.err.log 的落点。"""
    logs_dir = getattr(host, "logs_dir", None)
    if logs_dir:
        return Path(logs_dir)
    return Path(manager.scanner.reg_dir).parent / "logs"


def _tail_file(path: Path, name: str, n: int) -> dict:
    if not path.is_file():
        return {"name": name, "lines": []}
    size = path.stat().st_size
    with open(path, "rb") as fh:
        if size > 512 * 1024:          # 大日志只读尾部
            fh.seek(-512 * 1024, 2)
        raw = fh.read()
    try:
        text = raw.decode("utf-8")                       # py/ps1 输出主流 UTF-8
    except UnicodeDecodeError:
        text = raw.decode("gbk", errors="replace")       # cmd/exe 类输出常见 GBK
    lines = text.splitlines()[-n:]
    return {"name": name, "lines": lines}


def serve(manager, bind_host: str, port: int, controller=None, scheduler=None,
          cors_local_only: bool = True, settings_path=None,
          backup_settings=None):  # noqa: ANN001
    """绑定监听。controller 为 HostController（提供 meta/schedule），
    scheduler 为定时任务调度器（提供 snapshot/run_now）。

    cors_local_only：仅本机来源的网页可获得跨源许可（默认开）。关掉即回到
    「不回 CORS 头、不答预检」的历史行为，用于应急而不必回滚代码。

    端口重试：restart 时新宿主会在旧进程释放 1236 前反复重试 bind，实现无竞态交接。
    """
    import time
    handler = make_handler(manager, controller, scheduler, cors_local_only,
                           settings_path=settings_path,
                           backup_settings=backup_settings)
    last_err: OSError | None = None
    for _ in range(40):
        try:
            httpd = ThreadingHTTPServer((bind_host, port), handler)
            httpd.daemon_threads = True
            return httpd
        except OSError as exc:
            last_err = exc
            time.sleep(0.75)
    raise last_err  # 重试耗尽仍失败
