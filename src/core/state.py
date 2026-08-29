# -*- coding: utf-8 -*-
"""my-service · core/state.py

Manager：把 Scanner（发现）+ Runner（进程原子操作）编成一个带状态机的服务管理器。

状态机：stopped → starting → running → stopping → stopped
        任何阶段异常 → failed（附 error；failed 可 start 重入）
观测即真相：monitor 线程每秒用健康检查/进程存活校正内存态，
超时自动收敛，杜绝 早期内部版本 曾发生的 `_starting` 标记永久残留。
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from . import runner
from .scanner import Scanner, Spec

TICK_SEC = 1.0


class Manager:
    def __init__(self, scanner: Scanner):
        self.scanner = scanner
        self.specs: dict[str, Spec] = {}
        self.handles: dict[str, runner.ProcessHandle] = {}
        # svc -> 状态字典：status/started_at/error/fail_at
        self.state: dict[str, dict] = {}
        self._user_stopping: set[str] = set()   # 区分"用户主动停"与"崩溃"
        self._pending_stop: dict[str, float] = {}  # stop 发起时刻，stopping 超时兜底
        self.lock = threading.RLock()
        self.refresh()

    # ------------------------------------------------------------ 发现
    def refresh(self) -> None:
        with self.lock:
            specs = self.scanner.scan()
            self.specs = specs
            for name, spec in specs.items():
                if name not in self.state:
                    if not spec.valid:
                        self.state[name] = {"status": "invalid", "error": spec.error}
                    else:
                        self.state[name] = {
                            "status": "stopped", "started_at": None,
                            "error": None, "fail_at": None,
                        }
            for gone in [n for n in self.state if n not in specs]:
                # manifest 被删：无论进程是否存活都摘除条目并回收日志句柄
                h = self.handles.pop(gone, None)
                if h:
                    if h.popen.poll() is None:
                        runner.stop_managed(h)   # 内部 finally 会 close fh + 清 pid
                    else:
                        runner.close_handle(h)
                del self.state[gone]
            # 端口冲突检查（跨服务）
            seen: dict[int, str] = {}
            for name, spec in specs.items():
                port = spec.health_port
                if spec.valid and spec.health_type == "port" and port:
                    if port in seen and seen[port] != name:
                        st = self.state[name]
                        st["status"] = "invalid"
                        st["error"] = f"health.port {port} 与 {seen[port]} 冲突"
                    else:
                        seen[port] = name

    def _log_path(self, name: str) -> Path:
        # 兼容保留：服务自身日志归位于 <服务文件夹>/logs/service.log（见 runner.logs_dir）
        return runner.logs_dir(self.specs[name]) / "service.log"

    # ------------------------------------------------------------ 查询
    def snapshot(self) -> list[dict]:
        with self.lock:
            out = []
            for name, spec in sorted(self.specs.items()):
                st = self.state.get(name, {})
                status = st.get("status", "unknown")
                handle = self.handles.get(name)
                uptime = None
                if status == "running" and st.get("started_at"):
                    uptime = int(time.time() - st["started_at"])
                pid = None if handle is None else (handle.popen.poll() or handle.pid)
                if handle and handle.popen.poll() is not None:
                    pid = None
                out.append({
                    "name": name,
                    "display": spec.display,
                    "description": spec.description,
                    "status": status,
                    "port": spec.health_port if spec.health_type == "port" else None,
                    "url": spec.url,
                    "uptime_sec": uptime,
                    "autostart": spec.autostart,
                    "error": st.get("error"),
                })
            return out

    def get_spec(self, name: str) -> Spec | None:
        return self.specs.get(name)

    # ------------------------------------------------------------ 操作
    def start(self, name: str) -> tuple[bool, str]:
        with self.lock:
            spec = self.specs.get(name)
            st = self.state.get(name)
            if spec is None or st is None:
                return False, f"未知服务 {name}"
            if not spec.valid:
                return False, f"manifest 无效: {spec.error}"
            if not spec.enabled:
                return False, "服务已被 enabled:false 停用"
            cur = st["status"]
            if cur in ("running", "starting"):
                return True, f"已在 {cur}"
            try:
                handle = runner.start_process(spec)
            except Exception as exc:  # noqa: BLE001
                st.update(status="failed", error=f"启动失败: {exc}", fail_at=time.time())
                return False, str(exc)
            self.handles[name] = handle
            st.update(status="starting", started_at=handle.started_at, error=None, fail_at=None)
            return True, "starting"

    def stop(self, name: str) -> tuple[bool, str]:
        # 阶段一（持锁）：标记 stopping 并把 handle 从登记表摘除——
        # 此后 monitor 对该服务的 starting/running 分支因 handle 缺席而不再干预
        with self.lock:
            spec = self.specs.get(name)
            st = self.state.get(name)
            handle = self.handles.pop(name, None)
            if spec is None or st is None:
                return False, f"未知服务 {name}"
            if (st["status"] in ("stopped", "failed", "invalid")) and handle is None:
                if not st["status"].startswith("invalid"):
                    st.update(status="stopped", error=None, started_at=None)
                return True, "已停止"
            self._user_stopping.add(name)
            self._pending_stop[name] = time.time()
            st["status"] = "stopping"

        # 阶段二（锁外）：阻塞式收割进程，不占用 RLock 妨碍其他请求
        ok, msg = True, "stopped"
        try:
            if spec.mode == "managed":
                if handle:
                    runner.stop_managed(handle)
            elif spec.stop_script_abs:
                runner.run_stop_script(spec)
                if handle:
                    runner.stop_managed(handle)
        except Exception as exc:  # noqa: BLE001
            ok, msg = False, str(exc)

        # 阶段三（持锁）：收敛终态；服务可能已被 refresh 移除，判空防护
        with self.lock:
            self._user_stopping.discard(name)
            self._pending_stop.pop(name, None)
            st2 = self.state.get(name)
            if st2 is not None and not ok and msg:
                st2["error"] = f"停止异常: {msg}"
            if st2 is not None:
                st2["status"] = "stopped"
                st2["started_at"] = None
        return ok, msg

    def restart(self, name: str) -> tuple[bool, str]:
        ok, msg = self.stop(name)
        if not ok:
            return ok, msg
        return self.start(name)

    def stop_all(self) -> None:
        """收割当前所有托管中的服务（宿主退出/重启前调用）。"""
        for name in list(self.handles.keys()):
            try:
                self.stop(name)
            except Exception:  # noqa: BLE001
                pass

    def toggle_autostart(self, name: str, value: bool) -> tuple[bool, str]:
        with self.lock:
            spec = self.specs.get(name)
            if spec is None:
                return False, f"未知服务 {name}"
            try:
                raw = json.loads(spec.manifest_path.read_text(encoding="utf-8-sig"))
            except Exception as exc:  # noqa: BLE001
                return False, f"读 manifest 失败: {exc}"
            raw["autostart"] = bool(value)
            try:
                import os
                import tempfile

                text = json.dumps(raw, ensure_ascii=False, indent=2)
                # 先写临时文件再原子替换，避免中途崩溃损坏 manifest
                fd, tmp = tempfile.mkstemp(dir=str(spec.manifest_path.parent),
                                           suffix=".tmp")
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fh.write(text + "\n")
                os.replace(tmp, spec.manifest_path)
            except Exception as exc:  # noqa: BLE001
                return False, f"写 manifest 失败: {exc}"
            spec.autostart = bool(value)
            return True, "ok"

    # ------------------------------------------------------------ 监测循环
    def monitor_loop(self) -> None:
        """每秒校正一次观测态——唯一事实源在这里收敛。"""
        while True:
            try:
                self._tick()
            except Exception:  # noqa: BLE001 - 监测线程永不退出
                pass
            time.sleep(TICK_SEC)

    def _tick(self) -> None:
        with self.lock:
            self.refresh()  # 接住新建/删除的 manifest
            now = time.time()
            for name, spec in list(self.specs.items()):
                st = self.state[name]
                status = st["status"]
                handle = self.handles.get(name)

                if status == "starting" and handle:
                    alive = handle.popen.poll() is None
                    healthy, why = runner.check_health(spec, handle)
                    timeout = spec.health_timeout
                    elapsed = now - handle.started_at
                    if healthy:
                        st["status"], st["error"] = ("running", None)
                    elif not alive:
                        rc = handle.popen.returncode
                        self.handles.pop(name, None)
                        runner.close_handle(handle)
                        st.update(status="failed", error=f"进程启动即退出 rc={rc}", fail_at=now)
                    elif elapsed > timeout:
                        # 关键修复点：超时必须收敛，不能像 早期内部版本 那样残留 starting
                        st.update(status="failed",
                                  error=f"健康检查超时({timeout:.0f}s){('，' + why) if why else ''}",
                                  fail_at=now)

                elif status == "running" and handle:
                    if handle.popen.poll() is not None:
                        rc = handle.popen.returncode
                        crashed = name not in self._user_stopping
                        self.handles.pop(name, None)
                        runner.close_handle(handle)
                        if crashed:
                            st.update(status="failed", error=f"进程意外退出 rc={rc}",
                                      fail_at=now)
                        else:
                            st.update(status="stopped", started_at=None, error=None)
                        continue
                    if spec.health_type in ("port", "http") and now - float(st.get("last_check") or 0) >= 5:
                        # running 态每 5 秒复检一次健康端口，端口掉线即失败提示
                        st["last_check"] = now
                        healthy, why = runner.check_health(spec, handle)
                        if not healthy:
                            st["error"] = why or "健康检查失败(复检)"

                elif status == "stopping":
                    began = self._pending_stop.get(name)
                    if began and now - began > runner._STOP_WAIT_SEC + 6:
                        st.update(status="stopped", started_at=None)  # 兜底收敛
