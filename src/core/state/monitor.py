"""my-service · core/state/monitor.py

每秒观测收敛循环与 on_demand 空闲回收。

观测即真相：monitor 线程每秒用健康检查 / 进程存活校正内存态，超时自动收敛，
杜绝 `starting` 标记永久残留。回收前必须确认"要停的是本服务实例"。
"""
from __future__ import annotations

import contextlib
import threading
import time

from .. import audit, runner
from ..scanner import Spec
from ._common import _dur


class MonitorMixin:
    def _reap_idle(self, name: str) -> None:
        """on_demand 空闲回收：条件复核后走正规 stop 流程（优雅停 + 状态回 stopped）。

        回收前必须确认"要停的是本服务的实例"，两条信任来源：

        - **句柄在场** → 直接信任：那是本进程亲手拉起的，不可能是别人
        - **句柄缺席**（宿主重启后的接管场景）→ 回退到身份判定，只有 mine 才回收

        v0.11 的问题不在于"守不守句柄"，而在于判据只有"端口有人应答"——那既
        认不出孤儿（宿主重启后句柄丢失 → 判成外人 → 永远不回收），也可能误把
        占着同端口的别人的程序当成自家实例去停。现在统一按身份收口。
        """
        with self.lock:
            st = self.state.get(name)
            spec = self.specs.get(name)
            if (st is None or spec is None or st["status"] != "running"
                    or spec.mode != "on_demand" or name in self._idle_reaping):
                return
            if name not in self.handles:
                # 无句柄：须先自证是本服务的实例，否则可能误停别人的进程
                verdict, _via, _why = self.identify(name)
                if verdict != "mine":
                    return
            self._idle_reaping.add(name)
        try:
            self.stop(name, act="idle_reclaim",
                      reason=f"空闲超 {int(spec.idle_timeout)}s，按需服务自动回收")
        finally:
            with self.lock:
                self._idle_reaping.discard(name)

    # ------------------------------------------------------------ 监测循环
    def note_panel(self) -> None:
        """面板心跳：只在 /api/panel/ping 调用，脚本/API 调用不算活跃。"""
        self.panel_last_seen = time.time()

    def wake(self) -> None:
        """写操作后唤醒 monitor，立即收敛一次（不必等空闲间隔）。"""
        self._wake.set()

    def monitor_loop(self) -> None:
        """观测态收敛：面板活跃 1s / 空闲 10s；写操作可立即唤醒。"""
        while True:
            with contextlib.suppress(Exception):   # 监测线程永不退出
                self._tick()
            active = (time.time() - self.panel_last_seen) < self.settings["panel_grace_sec"]
            interval = self.settings["fast_sec"] if active else self.settings["idle_sec"]
            self._wake.wait(interval)
            self._wake.clear()
            # 登记簿清理：随 monitor 顺带执行（不再单独起线程）
            now = time.time()
            if now - self._last_prune >= 3600:
                self._last_prune = now
                with contextlib.suppress(Exception):   # 清理失败不影响收敛
                    audit.prune(float(self.settings.get("audit_retention_days", 7)))

    def _tick(self) -> None:
        with self.lock:
            self.refresh()  # 接住新建/删除的 manifest
            now = time.time()
            for name, spec in list(self.specs.items()):
                self._tick_one(name, spec, self.state[name],
                               self.handles.get(name), now)

    def _tick_one(self, name: str, spec: Spec, st: dict, handle: object, now: float) -> None:
        """按状态分发；各分支自行收敛，互不干扰。"""
        status = st["status"]
        if status == "starting":
            self._tick_starting(name, spec, st, handle, now)
        elif status == "failed" and spec.start_detach:
            self._tick_failed_detached(name, spec, st, now)
        elif status == "running":
            self._tick_running(name, spec, st, handle, now)
        elif status == "stopped":
            self._tick_stopped(name, st, now)
        elif status == "stopping":
            self._tick_stopping(name, st, now)

    # ------------------------------------------------------------ starting
    def _tick_starting(self, name: str, spec: Spec, st: dict, handle: object,
                       now: float) -> None:
        began = st.get("start_began")
        dur = _dur(float(began)) if began else None
        via = str(st.get("start_via") or "api")
        alive = handle.popen.poll() is None if handle else False
        # 快速可达判定，不做完整 urlopen（无监听端口可能被代理/TUN 拖成数秒，
        # 会卡住整个 monitor 循环）；running 后仍由复检兜底核对真健康
        healthy = runner.quick_alive(spec, handle)
        if healthy:
            self._mark_running(name, spec, st, handle, dur, via)
            return
        why = "健康目标未就绪"
        if spec.start_detach and handle and not alive:
            self._detach_fire_and_forget(name, st, handle)
        if spec.mode == "managed" and not spec.start_detach and not alive:
            self._mark_crash(name, st, handle, dur, via, now)
            return
        elapsed = now - float(st.get("started_at") or now)
        if elapsed > spec.health_timeout:
            self._mark_start_timeout(name, st, dur, via, why, spec.health_timeout, now)

    def _mark_running(self, name: str, spec: Spec, st: dict, handle: object,
                      dur: int | None, via: str) -> None:
        st["status"], st["error"] = ("running", None)
        st.pop("start_began", None)
        st.pop("start_via", None)
        st.pop("entry_note", None)   # 起来了就不必留失败线索
        audit.emit("start", svc=name, dur_ms=dur,
                   pid=handle.pid if handle else None,
                   note=f"{spec.mode} 健康就绪（{via}）")

    def _detach_fire_and_forget(self, name: str, st: dict, handle: object) -> None:
        """点火器脚本退场 = 正常；摘句柄，但把它说了什么留档。

        起不来的真相往往只在它的输出里（如官方脚本凭一个被回收复用的 pid
        喊 "already running" 然后退出 0，框架只能看到 60 秒健康超时）。
        """
        rc = handle.popen.returncode
        tail = runner.read_output_tail(handle.output_path)
        if tail:
            st["entry_note"] = f"入口脚本 rc={rc}：{tail}"
        self.handles.pop(name, None)
        runner.close_handle(handle)

    def _mark_crash(self, name: str, st: dict, handle: object, dur: int | None,
                    via: str, now: float) -> None:
        rc = handle.popen.returncode if handle else None
        self.handles.pop(name, None)
        if handle:
            runner.close_handle(handle)
        st.update(status="failed", error=f"进程启动即退出 rc={rc}", fail_at=now)
        st.pop("start_began", None)
        st.pop("start_via", None)
        audit.emit("crash", ok=False, svc=name, dur_ms=dur,
                   note=f"进程启动即退出 rc={rc}（{via}）")

    def _mark_start_timeout(self, name: str, st: dict, dur: int | None, via: str,
                            why: str, timeout: float, now: float) -> None:
        # 关键修复点：超时必须收敛，不能像早期版本那样残留 starting
        note = (f"健康检查超时({timeout:.0f}s)"
                f"{('，' + why) if why else ''}（{via}）")
        extra = str(st.pop("entry_note", "") or "")
        if extra:
            note = f"{note} | {extra}"
        st.update(status="failed", error=note, fail_at=now)
        st.pop("start_began", None)
        st.pop("start_via", None)
        audit.emit("start", ok=False, svc=name, dur_ms=dur, note=note)

    # ------------------------------------------------------------ failed
    def _tick_failed_detached(self, name: str, spec: Spec, st: dict, now: float) -> None:
        """假失败自愈：detached 型被外部拉起/升级慢未就绪时，健康通过就归位 running。"""
        if spec.probe not in ("port", "http"):
            return
        alive, _w = runner.check_health(spec, None)
        if alive:
            st.update(status="running", started_at=now, error=None)
            audit.emit("health_flip", svc=name,
                       note="假失败自愈：健康探测通过，归位 running")

    # ------------------------------------------------------------ running
    def _tick_running(self, name: str, spec: Spec, st: dict, handle: object,
                      now: float) -> None:
        if spec.start_detach:
            self._recheck_detached_identity(name, spec, st, handle, now)
            return
        if handle and handle.popen.poll() is not None:
            self._handle_unexpected_exit(name, st, handle, now)
            return
        since_check = now - float(st.get("last_check") or 0)
        if spec.probe in ("port", "http") and since_check >= 5:
            self._recheck_health(name, spec, st, handle, now)

    def _recheck_detached_identity(self, name: str, spec: Spec, st: dict,
                                   handle: object, now: float) -> None:
        """点火器型：运行中按**身份**复检（点火器早已退场，句柄不可作数）。"""
        if handle and handle.popen.poll() is not None:
            self.handles.pop(name, None)
            runner.close_handle(handle)
        due = now - float(st.get("last_check") or 0) >= 5
        if not (spec.probe in ("port", "http") and due):
            return
        st["last_check"] = now
        verdict, _via, why = self.identify(name)
        if verdict == "foreign":
            # 端口被别的程序接手：不再冒充运行中
            st.update(status="stopped", error=why, fail_at=now, port_foreign=True)
            audit.emit("identity_mismatch", ok=False, svc=name, note=why)
        elif verdict == "unknown":
            st["error"] = why
            if not st.get("ident_noted"):
                st["ident_noted"] = True
                audit.emit("identity_unknown", svc=name, note=why)
        elif verdict == "absent":
            st.update(status="stopped", started_at=None,
                      error="健康检查失联，进程可能已被外部停止")
            audit.emit("health_flip", ok=False, svc=name,
                       note="健康失联：进程可能已被外部停止")

    def _handle_unexpected_exit(self, name: str, st: dict, handle: object,
                                now: float) -> None:
        rc = handle.popen.returncode
        crashed = name not in self._user_stopping
        self.handles.pop(name, None)
        runner.close_handle(handle)
        if crashed:
            st.update(status="failed", error=f"进程意外退出 rc={rc}", fail_at=now)
            audit.emit("crash", ok=False, svc=name, note=f"进程意外退出 rc={rc}")
        else:
            st.update(status="stopped", started_at=None, error=None)

    def _recheck_health(self, name: str, spec: Spec, st: dict, handle: object,
                        now: float) -> None:
        """running 态每 5 秒复检一次健康端口；不依赖 handle（外部归位实例也能监控）。"""
        st["last_check"] = now
        if spec.mode == "on_demand":
            healthy, why = self._recheck_on_demand(name, spec, st, handle, now)
        else:
            healthy, why = runner.check_health(spec, handle)
        if healthy:
            st.pop("flip_noted", None)   # 恢复后允许再记一次翻转
            return
        self._note_unhealthy(name, st, handle, why, now)

    def _recheck_on_demand(self, name: str, spec: Spec, st: dict, handle: object,
                           now: float) -> tuple[bool, str]:
        """按需服务：复检顺带解析 last_active，并按空闲阈值回收。"""
        healthy, why, last_active = runner.check_health_ext(spec, handle)
        if last_active and last_active > 0:
            st["last_active"] = last_active
            basis = last_active
        else:
            basis = max(st.get("started_at") or 0, st.get("adopted_at") or 0) or None
        if (healthy and spec.idle_timeout > 0 and basis
                and (now - float(basis)) > spec.idle_timeout):
            threading.Thread(
                target=self._reap_idle, args=(name,), daemon=True).start()
        return healthy, why

    def _note_unhealthy(self, name: str, st: dict, handle: object, why: str,
                        now: float) -> None:
        if handle is not None:
            st["error"] = why or "健康检查失败(复检)"
            # 状态未翻转（仍 running）：只记第一次，否则每 5 秒刷一条噪音
            if not st.get("flip_noted"):
                st["flip_noted"] = True
                audit.emit("health_flip", ok=False, svc=name,
                           note=why or "健康检查失败(复检)")
            return
        # 外部归位实例（无句柄）：再核一次身份，避免对别人的进程做无权操作
        verdict, _via, iwhy = self.identify(name)
        if verdict == "foreign":
            st.update(status="stopped", error=iwhy, fail_at=now, port_foreign=True)
            audit.emit("identity_mismatch", ok=False, svc=name, note=iwhy)
        elif verdict == "mine":
            st.update(status="stopped", started_at=None, error=None)
            audit.emit("health_flip", ok=False, svc=name,
                       note="本服务实例已失联，归位 stopped")
        else:
            st["error"] = iwhy or why or "健康检查失败(复检)"

    # ------------------------------------------------------------ stopped / stopping
    def _tick_stopped(self, name: str, st: dict, now: float) -> None:
        """外部/手动启动的实例：定期按**身份**探测，确认是本服务才归位 running。"""
        if now - float(st.get("last_check") or 0) < 5:
            return
        st["last_check"] = now
        verdict, via, reason = self.identify(name)
        if verdict == "mine":
            self.adopt_external(name, f"检测到本服务实例：{reason}", via=via)
        elif verdict == "foreign":
            # 端口被别的程序占着：如实标注，不冒充、也不无权停它
            st.update(status="stopped", error=reason, fail_at=now, port_foreign=True)
            audit.emit("identity_mismatch", ok=False, svc=name, note=reason)
        # unknown / absent：保持 stopped，不动

    def _tick_stopping(self, name: str, st: dict, now: float) -> None:
        began = self._pending_stop.get(name)
        if began and now - began > runner._STOP_WAIT_SEC + 6:
            st.update(status="stopped", started_at=None)  # 兜底收敛
