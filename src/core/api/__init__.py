"""my-service · core/api.py

HTTP 层：REST API + web/ 静态托管。绑定 127.0.0.1，零第三方依赖。
"""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from .. import audit, backup, update
from .cors import (is_allowed_origin, is_local_origin, is_write_path,
                   origin_host, origin_of)
from .static import load as load_static

# 兼容旧私有名（原 core/api.py 的模块级纯函数，保持外部引用不破）
_origin_of = origin_of
_origin_host = origin_host
_is_local_origin = is_local_origin
_is_allowed_origin = is_allowed_origin
_is_write_path = is_write_path


class _Handler(BaseHTTPRequestHandler):
    # 由 make_handler 在子类上绑定（每个实例一套，避免多实例相互串味）
    manager = None
    host = None
    scheduler = None
    settings_path = None
    backup_settings = None
    cors_local_only: bool = True
    cors_allow: tuple[str, ...] = ()
    server_version = "my-service"
    protocol_version = "HTTP/1.1"

    # ---------------------------------------------------------- 工具
    def _allowed(self, origin: str) -> bool:
        """来源是否放行：本机，或命中 cors_allow 白名单。"""
        return _is_allowed_origin(origin, self.cors_allow)

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
        return (origin, True) if self._allowed(origin) else ("", False)

    def _denied(self, path: str) -> bool:
        """外站是否应被拒绝：仅拦写接口，且仅在开关开启时。"""
        if not self.cors_local_only:
            return False
        origin = _origin_of(self.headers)
        if origin is None or self._allowed(origin):
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
            "/api/update/check": self._get_update_check,
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
            "server": {"host": getattr(self, "bind_host", "127.0.0.1"),
                       "port": getattr(self, "bind_port", 1236)},
            "cors": {"local_only": bool(self.cors_local_only),
                     "allow": list(self.cors_allow or ())},
            "audit": {"retention_days": self._audit_days()},
            "backup": {"keep": self._backup_keep()},
            "update": {"mirrors": list(self.update_mirrors or ())},
            "advanced": {
                "reg_dir": self._reg_dir_name(),
                "monitor": self._monitor_settings(),
                "scheduler": {"tick_sec": getattr(self.scheduler, "tick_sec", 10.0)},
            },
        })

    def _audit_days(self) -> float:
        return float(getattr(self.manager, "settings", {}).get(
            "audit_retention_days", 7.0))

    def _monitor_settings(self) -> dict:
        settings = getattr(self.manager, "settings", {}) or {}
        return {key: settings.get(key, default) for key, default in
                (("fast_sec", 1.0), ("idle_sec", 10.0), ("panel_grace_sec", 15.0))}

    def _reg_dir_name(self) -> str:
        if not self.settings_path:
            return "reg"
        try:
            cfg = json.loads(Path(self.settings_path).read_text(encoding="utf-8-sig"))
            adv = cfg.get("advanced") if isinstance(cfg.get("advanced"), dict) else {}
            return str(adv.get("reg_dir") or "reg")
        except Exception:  # noqa: BLE001
            return "reg"

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
        """控制面登记簿（data/logs/host.jsonl）——不含任何服务业务输出。"""
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
        """宿主自身异常栈（data/logs/host.err.log）。"""
        qs = parse_qs(parsed.query)
        n = _num(qs, "n", 200)
        return self._json(200, _tail_file(
            _host_logs_dir(self.manager, self.host) / "host.err.log", "(host-err)", n))

    def _get_crons(self, parsed) -> None:  # noqa: ANN001
        crons = self.scheduler.snapshot() if self.scheduler else []
        return self._json(200, {"crons": crons})

    def _get_update_check(self, parsed) -> None:  # noqa: ANN001
        """GET /api/update/check —— 拉 GitHub Release 的 latest.json 对比版本。

        任何部署形态都允许检查与升级；源码工作区额外返回 source_worktree=true，
        前端据此提示「更新后工作区将与 git 不一致」。
        """
        from ..host import VERSION
        home = getattr(self.host, "home", None)
        if self.host is None or home is None:
            self._json(400, {"ok": False, "error": "宿主上下文缺失"})
            return
        try:
            info = update.check(VERSION, mirrors=self.update_mirrors)
        except Exception as exc:  # noqa: BLE001 - 网络/格式异常统一报 502
            self._json(502, {"ok": False, "error": f"检查更新失败: {exc}"})
            return
        info["source_worktree"] = update.is_source_worktree(home)
        self._json(200, info)

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
            or self._post_host_autostart(parts, body)
            or self._post_host_action(parts)
            or self._post_audit_clear(parts, body)
            or self._post_crons_order(parts, body)
            or self._post_cron_run(parts, parsed)
            or self._post_cron_config(parts, body)
            or self._post_autostart(parts, body)
            or self._post_update_apply(parts, body)
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
        """POST /api/settings {"cors":{}, "audit":{}, "backup":{}, "update":{}, "advanced":{}}"""
        if not self._is(parts, "api", "settings"):
            return False
        error = self._settings_patch_error(body)
        if error is not None:
            self._json(400, {"ok": False, "error": error})
            return True
        if self.settings_path and not self._persist_settings():
            return True
        self._json(200, {"ok": True})
        return True

    def _settings_patch_error(self, body: object) -> str | None:
        """应用到各配置组；返回错误消息（None = 成功）。"""
        if not isinstance(body, dict):
            return "请求体需为对象"
        if "server" in body:
            return "server 组需改 config.json 并重启宿主"
        groups = ("cors", "audit", "backup", "update", "advanced")
        if not any(isinstance(body.get(g), dict) for g in groups):
            return "没有可修改的配置组"
        if isinstance(body.get("cors"), dict):
            self._apply_cors(body["cors"])
        errors: list[str | None] = []
        if isinstance(body.get("audit"), dict):
            errors.append(self._apply_audit(body["audit"]))
        if isinstance(body.get("backup"), dict):
            errors.append(self._apply_backup_keep(body["backup"]))
        if isinstance(body.get("update"), dict):
            errors.append(self._apply_update_mirrors(body["update"]))
        if isinstance(body.get("advanced"), dict):
            errors.append(self._apply_advanced(body["advanced"]))
        return next((e for e in errors if e), None)

    def _apply_cors(self, cors: dict) -> None:
        local_only = cors.get("local_only")
        if isinstance(local_only, bool):
            type(self).cors_local_only = local_only
        allow = cors.get("allow")
        if isinstance(allow, list):
            type(self).cors_allow = tuple(
                str(item) for item in allow if str(item).strip())

    def _apply_audit(self, audit_cfg: dict) -> str | None:
        if "retention_days" not in audit_cfg:
            return None
        try:
            days = float(audit_cfg["retention_days"])
        except (TypeError, ValueError):
            return "retention_days 需为数字"
        if not (0 <= days <= 3650):
            return "retention_days 需在 0 ~ 3650 天之间"
        self.manager.settings["audit_retention_days"] = days
        return None

    def _apply_update_mirrors(self, update_cfg: dict) -> str | None:
        mirrors = update_cfg.get("mirrors")
        if not isinstance(mirrors, list):
            return "mirrors 需为数组"
        type(self).update_mirrors = [
            str(item).strip() for item in mirrors if str(item).strip()]
        return None

    def _apply_advanced(self, adv: dict) -> str | None:
        """monitor / scheduler 可热改；reg_dir 需改文件并重启。"""
        if "reg_dir" in adv:
            return "reg_dir 需改 config.json 并重启宿主"
        mon = adv.get("monitor")
        if isinstance(mon, dict):
            for key in ("fast_sec", "idle_sec", "panel_grace_sec"):
                if key not in mon:
                    continue
                try:
                    val = float(mon[key])
                except (TypeError, ValueError):
                    return f"{key} 需为数字"
                self.manager.settings[key] = max(0.2, val)
        sch = adv.get("scheduler")
        if isinstance(sch, dict) and "tick_sec" in sch:
            try:
                self.scheduler.tick_sec = max(1.0, float(sch["tick_sec"]))
            except (TypeError, ValueError):
                return "tick_sec 需为数字"
        return None

    def _apply_backup_keep(self, bkp: object) -> str | None:
        if not (isinstance(bkp, dict) and "keep" in bkp):
            return None
        try:
            keep = int(float(bkp["keep"]))
        except (TypeError, ValueError):
            return "备份保留份数需为数字"
        if not (0 <= keep <= 365):
            return "备份保留份数需在 0 ~ 365 之间（0 = 不自动清理）"
        if self.backup_settings is not None:
            self.backup_settings["keep"] = keep
            # 立即按新策略收敛一次，保存后 data/bak/ 里马上只剩 keep 份
            if self.settings_path:
                backup.prune_backups(Path(self.settings_path).parent, keep)
        return None

    def _persist_settings(self) -> bool:
        """把当前生效值写回 config.json（分组结构）；失败时已发出 500。"""
        try:
            sp = Path(self.settings_path)
            cfg = (json.loads(sp.read_text(encoding="utf-8-sig"))
                   if sp.is_file() else {})
            cfg["cors"] = {
                "local_only": bool(self.cors_local_only),
                "allow": list(self.cors_allow or ()),
            }
            cfg["audit"] = {
                "retention_days": self.manager.settings.get("audit_retention_days", 7)}
            cfg["backup"] = {**cfg.get("backup", {}), "keep": self._backup_keep()}
            if self.update_mirrors is not None:
                cfg["update"] = {"mirrors": list(self.update_mirrors)}
            adv = cfg.get("advanced") if isinstance(cfg.get("advanced"), dict) else {}
            adv["monitor"] = {k: self.manager.settings[k] for k in
                              ("fast_sec", "idle_sec", "panel_grace_sec")}
            adv["scheduler"] = {"tick_sec": getattr(self.scheduler, "tick_sec", 10.0)}
            cfg["advanced"] = adv
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

    def _post_host_autostart(self, parts: list[str], body: dict) -> bool:
        """POST /api/host/autostart {"enabled": bool} —— 启动文件夹自启开关。

        只做"启动文件夹"这一种（无需管理员权限）；计划任务 / SYSTEM / Nssm
        仍走 run/install_autostart.ps1。必须排在 /api/host/{stop|restart} 之前。
        """
        if not self._is(parts, "api", "host", "autostart"):
            return False
        if self.host is None:
            self._json(501, {"ok": False, "error": "宿主自管理不可用"})
            return True
        enabled = body.get("enabled")
        if not isinstance(enabled, bool):
            self._json(400, {"ok": False, "error": '需要布尔字段 "enabled"'})
            return True
        ok, info = self.host.set_autostart(enabled)
        if not ok:
            self._json(500, {"ok": False, "error": info})
            return True
        self._json(200, {"ok": True, "autostart": info})
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

    def _post_update_apply(self, parts: list[str], body: dict) -> bool:
        """POST /api/update/apply {version,url,sha256} —— 下载暂存到 data/update/code.new。

        前端下载成功后会自动调 `/api/host/restart`；旧宿主派生 `apply-update-and-start`
        更新器，等本进程退出后按清单替换 `src/ web/ run/ docs/` + 三文档
        （旧代码留 data/bak/code-prev 可回滚），再起新宿主。
        任何部署形态都可下发；源码工作区替换后与 git 不一致，可 `git checkout .` 恢复。
        """
        if not self._is(parts, "api", "update", "apply"):
            return False
        home = getattr(self.host, "home", None)
        if self.host is None or home is None:
            self._json(400, {"ok": False, "error": "宿主上下文缺失"})
            return True
        result = update.apply(home, body, mirrors=self.update_mirrors)
        self._json(200 if result.get("ok") else 500, result)
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


def make_handler(manager, host=None, scheduler=None,  # noqa: PLR0913, ANN001
                 cors_local_only: bool = True,
                 settings_path=None, backup_settings=None,
                 cors_allow=None, update_mirrors=None,
                 bind_host: str = "127.0.0.1", bind_port: int = 1236):
    """绑定依赖后返回 Handler 子类（依赖走类属性，方法体不依赖闭包）。"""
    return type("Handler", (_Handler,), {
        "manager": manager,
        "host": host,
        "scheduler": scheduler,
        "settings_path": settings_path,
        "backup_settings": backup_settings,
        "cors_local_only": cors_local_only,
        # 非 list/tuple 一律视为空，避免字符串被拆成单字符
        "cors_allow": tuple(cors_allow) if isinstance(cors_allow, (list, tuple)) else (),
        "update_mirrors": list(update_mirrors) if isinstance(update_mirrors, (list, tuple)) else None,
        "bind_host": bind_host,
        "bind_port": bind_port,
    })


def _num(qs: dict, key: str, default: int) -> int:
    try:
        return max(1, min(1000, int(qs.get(key, [str(default)])[0])))
    except (TypeError, ValueError):
        return default


def _host_logs_dir(manager, host) -> Path:  # noqa: ANN001
    """宿主记录目录：<my-service>/data/logs —— 登记簿 host.jsonl / 异常栈 host.err.log 的落点。"""
    logs_dir = getattr(host, "logs_dir", None)
    if logs_dir:
        return Path(logs_dir)
    return Path(manager.scanner.reg_dir).parent.parent / "data" / "logs"


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


def serve(manager, bind_host: str, port: int, controller=None, scheduler=None,  # noqa: PLR0913
          cors_local_only: bool = True, settings_path=None,
          backup_settings=None, cors_allow=None,
          update_mirrors=None):  # noqa: ANN001
    """绑定监听。controller 为 HostController（提供 meta/schedule），
    scheduler 为定时任务调度器（提供 snapshot/run_now）。

    cors_local_only：仅本机来源的网页可获得跨源许可（默认开）。关掉即回到
    「不回 CORS 头、不答预检」的历史行为，用于应急而不必回滚代码。

    端口重试：restart 时新宿主会在旧进程释放 1236 前反复重试 bind，实现无竞态交接。
    """
    import time
    handler = make_handler(manager, controller, scheduler, cors_local_only,
                           settings_path=settings_path,
                           backup_settings=backup_settings,
                           cors_allow=cors_allow,
                           update_mirrors=update_mirrors,
                           bind_host=bind_host, bind_port=port)
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
