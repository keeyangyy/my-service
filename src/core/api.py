# -*- coding: utf-8 -*-
"""my-service · core/api.py

HTTP 层：REST API + web/ 静态托管。绑定 127.0.0.1，零第三方依赖。
"""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from . import runner

WEB_DIR = Path(__file__).resolve().parent.parent / "web"

MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".png": "image/png", ".ico": "image/x-icon",
}


def make_handler(manager):  # noqa: ANN001 - manager: core.state.Manager
    class Handler(BaseHTTPRequestHandler):
        server_version = "my-service/1.0"
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

            if path.startswith("/api/logs/"):
                name = path[len("/api/logs/"):]
                qs = parse_qs(parsed.query)
                try:
                    n = max(1, min(1000, int(qs.get("n", ["200"])[0])))
                except ValueError:
                    n = 200
                payload = read_log_tail(manager, name, n)
                if payload is None:
                    return self._json(404, {"ok": False, "error": f"未知服务 {name}"})
                return self._json(200, payload)

            if path == "/api/host-log":
                qs = parse_qs(parsed.query)
                try:
                    n = max(1, min(1000, int(qs.get("n", ["200"])[0])))
                except ValueError:
                    n = 200
                return self._json(200, read_host_log(manager, n))

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

            # POST /api/services/{name}/{start|stop|restart}
            if len(parts) == 4 and parts[0] == "api" and parts[1] == "services":
                name, action = parts[2], parts[3]
                fn = {"start": manager.start,
                      "stop": manager.stop,
                      "restart": manager.restart}.get(action)
                if fn is None:
                    return self._json(404, {"ok": False, "error": f"未知动作 {action}"})
                ok, msg = fn(name)
                status_now = next(
                    (s["status"] for s in manager.snapshot() if s["name"] == name), None)
                return self._json(202 if ok else 500,
                                  {"ok": ok, "message": msg, "status": status_now})

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


def read_log_tail(manager, name: str, n: int) -> dict | None:  # noqa: ANN001
    """服务自身日志：<服务文件夹>/run/service.log（stdout 为主，err 单独成文件）。"""
    spec = manager.specs.get(name)
    if spec is None:
        return None
    return _tail_file(runner.run_dir(spec) / "service.log", name, n)


def read_host_log(manager, n: int) -> dict:  # noqa: ANN001
    """宿主自身日志：<services_root>/my-service/run/service.log —— 只含启停事件。"""
    rdir = Path(manager.scanner.root) / "my-service" / "run"
    return _tail_file(rdir / "service.log", "(host)", n)


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


def serve(manager, host: str, port: int) -> ThreadingHTTPServer:
    handler = make_handler(manager)
    httpd = ThreadingHTTPServer((host, port), handler)
    httpd.daemon_threads = True
    return httpd
