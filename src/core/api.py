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

WEB_DIR = Path(__file__).resolve().parent.parent.parent / "web"

# ---------------------------------------------------------------- CORS / 外站门禁
# 背景：控制面绑定 127.0.0.1，本机任何程序（curl/脚本/服务）本来就能直接调用，
# 从来不需要额外开放。唯一受限的是"浏览器里的网页"——跨源时浏览器默认不把响应
# 交给页面 JS，带 JSON 的请求还会先发 OPTIONS 预检、没被应答就根本不发。
# 方案 S：判据只有一条"来源是否本机"——本机网页（任意端口）全放行且能读回结果；
# 外站网页一律拒绝（浏览器跨源 POST 必定带 Origin，故可可靠识别；不带 Origin 的
# 请求即 curl/脚本/同源面板，按本机程序处理，行为与历史完全一致）。
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]"}

# 会改变宿主或服务状态的接口（外站门禁只拦这些；读接口无需拦，读也读不到）
WRITE_PREFIXES = (
    "/api/services/", "/api/crons/", "/api/audit/clear", "/api/config/", "/api/host/",
)


def _origin_of(headers) -> str | None:
    """取出请求的 Origin；不存在返回 None（同源请求 / curl / 非浏览器客户端）。"""
    origin = (headers.get("Origin") or "").strip()
    return origin or None


def _origin_host(origin: str) -> str:
    """由 Origin 解析主机名（小写，去端口）。解析不出来返回空串。"""
    try:
        host = (urlparse(origin).hostname or "").lower()
    except ValueError:
        return ""
    return host


def _is_local_origin(origin: str) -> bool:
    """来源是否为本机。端口不限——本机任何端口起的页面都算自己人。"""
    return _origin_host(origin) in LOCAL_HOSTS


def _is_write_path(path: str) -> bool:
    return path.startswith(WRITE_PREFIXES)


MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".png": "image/png", ".ico": "image/x-icon",
}


def make_handler(manager, host=None, scheduler=None,
                 cors_local_only: bool = True):  # noqa: ANN001 - manager: core.state.Manager, host: HostController|None, scheduler: core.scheduler.Scheduler|None
    class Handler(BaseHTTPRequestHandler):
        server_version = "my-service"
        protocol_version = "HTTP/1.1"

        # ---------------------------------------------------------- 工具
        def _acao(self) -> tuple[str, bool]:
            """本请求的 CORS 处置：(回显的 Origin 或 '', 是否本地来源)。

            未带 Origin（同源 / curl / 非浏览器）→ 不加头，行为与历史完全一致。
            开关关闭 → 一律不加头，整体退回无 CORS 的历史语义。
            """
            if not cors_local_only:
                return "", True
            origin = _origin_of(self.headers)
            if origin is None:
                return "", True
            return (origin, True) if _is_local_origin(origin) else ("", False)

        def _denied(self, path: str) -> bool:
            """外站是否应被拒绝：仅拦写接口，且仅在开关开启时。"""
            if not cors_local_only:
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

            # 外站门禁：写接口统一在此拦截，避免逐条路由漏挂
            if self._denied(unquote(parsed.path)):
                return self._json(403, {"ok": False, "error": "cross-origin denied"})

            # POST /api/services/order  {"names": [...]}  —— 面板拖拽排序
            if len(parts) == 3 and parts[0] == "api" and parts[1] == "services" \
                    and parts[2] == "order":
                names = body.get("names")
                if not isinstance(names, list):
                    return self._json(400, {"ok": False, "error": '需要数组字段 "names"'})
                ok, code, msg = manager.set_order([str(n) for n in names])
                return self._json(200 if ok else 500, {"ok": ok, "message": msg})

            # POST /api/services/{name}/config  补丁式改配置：
            # {"idle_timeout_sec": N}（仅 on_demand 服务）
            # ★ 必须排在下面的 {start|stop|restart} 之前：同形路由，先到先得
            if len(parts) == 4 and parts[0] == "api" and parts[1] == "services" \
                    and parts[3] == "config":
                patch = {k: v for k, v in body.items() if k in ("idle_timeout_sec",)}
                if not patch:
                    return self._json(400, {
                        "ok": False,
                        "error": '至少提供 idle_timeout_sec'})
                ok, code, msg = manager.update_config(parts[2], patch)
                http_code = {"ok": 200, "not_found": 404, "invalid": 400,
                             "io": 500}.get(code, 500)
                return self._json(http_code, {"ok": ok, "message": msg,
                                              "error": None if ok else msg})

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

            # POST /api/crons/order  {"names": [...]}  —— 面板拖拽排序
            if len(parts) == 3 and parts[0] == "api" and parts[1] == "crons" \
                    and parts[2] == "order":
                if scheduler is None:
                    return self._json(501, {"ok": False, "error": "调度器不可用"})
                names = body.get("names")
                if not isinstance(names, list):
                    return self._json(400, {"ok": False, "error": '需要数组字段 "names"'})
                ok, code, msg = scheduler.set_order([str(n) for n in names])
                return self._json(200 if ok else 500, {"ok": ok, "message": msg})

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

            # POST /api/crons/{name}/config  补丁式改配置：
            # {"enabled":bool} / {"catch_up":bool} / {"timeout_sec":N} /
            # {"schedule":{"type":"daily","at":"HH:MM"}} 可任意组合
            if len(parts) == 4 and parts[0] == "api" and parts[1] == "crons" \
                    and parts[3] == "config":
                if scheduler is None:
                    return self._json(501, {"ok": False, "error": "调度器不可用"})
                patch = {k: v for k, v in body.items()
                         if k in ("enabled", "catch_up", "timeout_sec", "schedule")}
                if not patch:
                    return self._json(400, {
                        "ok": False, "error":
                            "至少提供 enabled / catch_up / timeout_sec / schedule 之一"})
                for flag in ("enabled", "catch_up"):
                    if flag in patch and not isinstance(patch[flag], bool):
                        return self._json(400, {"ok": False, "error": f"{flag} 需为布尔"})
                ok, code, msg = scheduler.update_config(parts[2], patch)
                http_code = {"ok": 200, "not_found": 404, "invalid": 400,
                             "io": 500}.get(code, 500)
                return self._json(http_code, {"ok": ok, "message": msg,
                                              "error": None if ok else msg})

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
          cors_local_only: bool = True):  # noqa: ANN001
    """绑定监听。controller 为 HostController（提供 meta/schedule），
    scheduler 为定时任务调度器（提供 snapshot/run_now）。

    cors_local_only：仅本机来源的网页可获得跨源许可（默认开）。关掉即回到
    「不回 CORS 头、不答预检」的历史行为，用于应急而不必回滚代码。

    端口重试：restart 时新宿主会在旧进程释放 1236 前反复重试 bind，实现无竞态交接。
    """
    import time
    handler = make_handler(manager, controller, scheduler, cors_local_only)
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
