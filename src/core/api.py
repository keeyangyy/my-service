# -*- coding: utf-8 -*-
"""my-service · core/api.py

HTTP 层：REST API + web/ 静态托管。绑定 127.0.0.1，零第三方依赖。
"""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from . import audit

WEB_DIR = Path(__file__).resolve().parent.parent / "web"

MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".png": "image/png", ".ico": "image/x-icon",
}


def make_handler(manager, host=None, scheduler=None):  # noqa: ANN001 - manager: core.state.Manager, host: HostController|None, scheduler: core.scheduler.Scheduler|None
    class Handler(BaseHTTPRequestHandler):
        server_version = "my-service"
        protocol_version = "HTTP/1.1"

        # ---------------------------------------------------------- 工具
        def _json(self, code: int, payload: dict) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _static(self, rel: str) -> None:
            path = (WEB_DIR / rel).resolve()
            if not path.is_file() or WEB_DIR.resolve() not in path.parents:
                return self._json(404, {"ok": False, "error": "not found"})
            body = path.read_bytes()
            mime = MIME.get(path.suffix.lower(), "application/octet-stream")
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *args):  # noqa: A003 - 静默访问日志
            pass

        # ---------------------------------------------------------- GET
        def do_GET(self):  # noqa: N802
            parsed = urlparse(self.path)
            path = unquote(parsed.path)

            if path == "/health":
                return self._json(200, {"ok": True})

            if path == "/api/host":
                if host is None:
                    return self._json(200, {"ok": True})
                return self._json(200, host.meta())

            if path == "/" or path == "/index.html":
                return self._static("index.html")

            if path.startswith("/api/services"):
                # /api/services 或 /api/services/{name}
                parts = [p for p in path.split("/") if p]  # ['api','services',name?]
                if len(parts) == 2:
                    return self._json(200, {"services": manager.snapshot()})
                name = parts[2]
                svc = next((s for s in manager.snapshot() if s["name"] == name), None)
                if svc is None:
                    return self._json(404, {"ok": False, "error": f"未知服务 {name}"})
                return self._json(200, svc)

            if path == "/api/audit":
                # 控制面登记簿（my-service\logs\host.jsonl）——不含任何服务业务输出
                qs = parse_qs(parsed.query)
                n = _num(qs, "n", 200)
                svc = (qs.get("svc") or [None])[0] or None
                only_fail = (qs.get("fail") or ["0"])[0].lower() in ("1", "true", "yes")
                return self._json(200, {
                    "ok": True,
                    "source": str(audit.current_path() or ""),
                    "events": audit.tail(n, svc, only_fail),
                })

            if path == "/api/host-err":
                # 宿主自身异常栈（my-service\logs\host.err.log）
                qs = parse_qs(parsed.query)
                n = _num(qs, "n", 200)
                return self._json(200, _tail_file(
                    _host_logs_dir(manager, host) / "host.err.log", "(host-err)", n))

            if path == "/api/crons":
                # 定时任务列表（reg\cron\*.json）
                crons = scheduler.snapshot() if scheduler else []
                return self._json(200, {"crons": crons})

            rest = path.lstrip("/")
            if rest and not rest.startswith("api/"):
                return self._static(rest)
            return self._json(404, {"ok": False, "error": "not found"})

        # ---------------------------------------------------------- POST
        def do_POST(self):  # noqa: N802
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except (TypeError, ValueError):
                return self._json(400, {"ok": False, "error": "Content-Length 非法"})
            raw = self.rfile.read(length) if length > 0 else b""
            try:
                body = json.loads(raw.decode("utf-8")) if raw else {}
            except Exception:  # noqa: BLE001
                return self._json(400, {"ok": False, "error": "请求体不是合法 JSON"})
            if not isinstance(body, dict):
                return self._json(400, {"ok": False, "error": "请求体必须是 JSON 对象"})

            parsed = urlparse(self.path)
            parts = [p for p in unquote(parsed.path).split("/") if p]

            # POST /api/services/{name}/{start|stop|restart}[?via=cli]
            if len(parts) == 4 and parts[0] == "api" and parts[1] == "services":
                name, action = parts[2], parts[3]
                if action not in ("start", "stop", "restart"):
                    return self._json(404, {"ok": False, "error": f"未知动作 {action}"})
                via = (parse_qs(parsed.query).get("via") or ["api"])[0]
                if action == "start":
                    ok, msg = manager.start(name, via=via)
                elif action == "stop":
                    ok, msg = manager.stop(name)
                else:
                    ok, msg = manager.restart(name, via=via)
                status_now = next(
                    (s["status"] for s in manager.snapshot() if s["name"] == name), None)
                return self._json(202 if ok else 500,
                                  {"ok": ok, "message": msg, "status": status_now})

            # POST /api/host/{stop|restart}  —— 宿主自身生命周期（专属通道，不进服务列表）
            if len(parts) == 3 and parts[0] == "api" and parts[1] == "host":
                action = parts[2]
                if host is None:
                    return self._json(501, {"ok": False, "error": "宿主自管理不可用"})
                if action not in ("stop", "restart"):
                    return self._json(404, {"ok": False, "error": f"未知宿主动作 {action}"})
                host.schedule(action)   # 延迟执行，先让本响应发出
                return self._json(202, {"ok": True, "action": action})

            # POST /api/audit/clear  {"svc": "name"} 或 {} 清空全部
            if len(parts) == 3 and parts[0] == "api" and parts[1] == "audit" \
                    and parts[2] == "clear":
                svc = body.get("svc")
                if svc is not None and not isinstance(svc, str):
                    return self._json(400, {"ok": False, "error": 'svc 需为字符串或 null'})
                removed = audit.clear(svc or None)
                return self._json(200, {"ok": True, "removed": removed})

            # POST /api/crons/{name}/run  —— 手动立即触发定时任务
            if len(parts) == 4 and parts[0] == "api" and parts[1] == "crons" \
                    and parts[3] == "run":
                if scheduler is None:
                    return self._json(501, {"ok": False, "error": "调度器不可用"})
                via = (parse_qs(parsed.query).get("via") or ["manual"])[0]
                ok, code, msg = scheduler.run_now(parts[2], via=via)
                http_code = {"ok": 202, "not_found": 404, "invalid": 400,
                             "busy": 409, "disabled": 409}.get(code, 500)
                return self._json(http_code, {"ok": ok, "message": msg})

            # POST /api/crons/{name}/config  {"enabled": bool}
            if len(parts) == 4 and parts[0] == "api" and parts[1] == "crons" \
                    and parts[3] == "config":
                if scheduler is None:
                    return self._json(501, {"ok": False, "error": "调度器不可用"})
                value = body.get("enabled")
                if not isinstance(value, bool):
                    return self._json(400, {"ok": False, "error": '需要布尔字段 "enabled"'})
                ok, msg = scheduler.toggle_enabled(parts[2], value)
                return self._json(200 if ok else 500, {"ok": ok, "error": None if ok else msg})

            # POST /api/config/{name}  {"autostart": bool}
            if len(parts) == 3 and parts[0] == "api" and parts[1] == "config":
                name = parts[2]
                value = body.get("autostart")
                if not isinstance(value, bool):
                    return self._json(400, {"ok": False, "error": '需要布尔字段 "autostart"'})
                ok, msg = manager.toggle_autostart(name, value)
                return self._json(200 if ok else 500, {"ok": ok, "error": None if ok else msg})

            return self._json(404, {"ok": False, "error": "not found"})

    return Handler


def _num(qs: dict, key: str, default: int) -> int:
    try:
        return max(1, min(1000, int(qs.get(key, [str(default)])[0])))
    except (TypeError, ValueError):
        return default


def _host_logs_dir(manager, host) -> Path:  # noqa: ANN001
    """宿主运行时产物目录：<my-service>/logs —— 框架侧文件的唯一落点。"""
    rdir = getattr(host, "rdir", None)
    if rdir:
        return Path(rdir)
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


def serve(manager, bind_host: str, port: int, controller=None, scheduler=None):  # noqa: ANN001
    """绑定监听。controller 为 HostController（提供 meta/schedule），
    scheduler 为定时任务调度器（提供 snapshot/run_now）。

    端口重试：restart 时新宿主会在旧进程释放 1236 前反复重试 bind，实现无竞态交接。
    """
    import time
    handler = make_handler(manager, controller, scheduler)
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
