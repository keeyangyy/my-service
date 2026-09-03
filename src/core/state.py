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
        self._idle_reaping: set[str] = set()    # on_demand 空闲回收进行中（防重复触发）
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
                    "mode": spec.mode,
                    "idle_timeout": spec.idle_timeout if spec.mode == "on_demand" else None,
                    "port": spec.health_port if spec.health_type == "port" else None,
                    "url": spec.url,
                    "uptime_sec": uptime,
                    "autostart": spec.autostart,
                    "error": st.get("error"),
                })
            return out

    def get_spec(self, name: str) -> Spec | None:
        return self.specs.get(name)

    # ------------------------------------------------------------ 外部运行判定
    def external_alive(self, name: str) -> tuple[bool, str]:
        """判断服务当前是否已有实例在运行（超出框架进程表的：外部/残留/手动）。

        判定依据：port/http → 快速端口探测；process → pidfile 存活；
        none / process 无 pidfile → 无法判定（返回 False，靠托管句柄）。
        port/http 用快速端口探测而非 urlopen——防双开只需知道"端口有无实例监听"，
        而 urlopen 在无监听端口上可能被代理/TUN 拖成数秒，拖慢启动。
        """
        spec = self.specs.get(name)
        if spec is None or not spec.valid:
            return False, "未注册"
        htype = spec.health_type
        if htype in ("port", "http"):
            if runner.quick_alive(spec, None):
                if htype == "port":
                    return True, f"端口 {spec.health_port} 已有实例监听"
                _, port = runner.parse_http_host_port(spec.health_url or "")
                return True, f"健康端口 {port} 已有实例监听"
            return False, "健康端口无监听"
        if htype == "process" and spec.pidfile:
            pf = spec.pidfile
            p = (spec.path / pf).resolve() if not Path(pf).is_absolute() else Path(pf)
            if p.is_file():
                try:
                    pid = int(p.read_text(encoding="ascii").strip())
                except Exception:  # noqa: BLE001
                    return False, "pidfile 内容非数字"
                if runner.pid_alive(pid):
                    return True, f"pidfile {pid} 存活"
                return False, f"pidfile {pid} 已不在运行"
            return False, "pidfile 不存在"
        return False, "无法判定（none/process 无 pidfile）"

    def adopt_external(self, name: str, reason: str) -> None:
        """把已在外部运行的实例归位为 running（自启跳过等场景，让状态如实反映）。"""
        with self.lock:
            st = self.state.get(name)
            if st is None:
                return
            if st["status"] != "running":
                st.update(status="running", started_at=st.get("started_at") or time.time(),
                          error=None, fail_at=None)

    # ------------------------------------------------------------ 操作
    def start(self, name: str) -> tuple[bool, str]:
        with self.lock:
            spec = self.specs.get(name)
            st = self.state.get(name)
            if spec is None or st is None:
                return False, f"未知服务 {name}"
            if not spec.valid:
                return False, f"注册条无效: {spec.error}"
            if not spec.enabled:
                return False, "服务已被 enabled:false 停用"
            cur = st["status"]
            if cur in ("running", "starting"):
                return True, f"已在 {cur}"
            # 防双开：冷启动（stopped/failed）前确认没有已存在的实例（外部/残留/手动），
            # 否则会双重拉起（some-service 实弹教训；process 型靠 pidfile 判定）。
            if cur in ("stopped", "failed"):
                alive, _reason = self.external_alive(name)
                if alive:
                    st.update(status="failed",
                              error=f"检测到已有实例在运行（{_reason}），已拒绝重复启动",
                              fail_at=time.time())
                    return False, f"检测到已有实例在运行（{_reason}），已拒绝重复启动"
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
            if spec.mode in ("managed", "on_demand"):
                # 先执行 stop_script 温和灭火（可选），再整树收割兜底
                if spec.stop_script_abs:
                    runner.run_stop_script(spec)
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

    def _reap_idle(self, name: str) -> None:
        """on_demand 空闲回收：条件复核后走正规 stop 流程（优雅停 + 状态回 stopped）。"""
        with self.lock:
            st = self.state.get(name)
            spec = self.specs.get(name)
            if (st is None or spec is None or st["status"] != "running"
                    or spec.mode != "on_demand" or name in self._idle_reaping):
                return
            self._idle_reaping.add(name)
        try:
            self.stop(name)
        finally:
            with self.lock:
                self._idle_reaping.discard(name)

    def toggle_autostart(self, name: str, value: bool) -> tuple[bool, str]:
        with self.lock:
            spec = self.specs.get(name)
            if spec is None:
                return False, f"未知服务 {name}"
            try:
                raw = json.loads(spec.reg_path.read_text(encoding="utf-8-sig"))
            except Exception as exc:  # noqa: BLE001
                return False, f"读注册条失败: {exc}"
            raw["autostart"] = bool(value)
            try:
                import os
                import tempfile

                text = json.dumps(raw, ensure_ascii=False, indent=2)
                # 先写临时文件再原子替换，避免中途崩溃损坏注册条
                fd, tmp = tempfile.mkstemp(dir=str(spec.reg_path.parent),
                                           suffix=".tmp")
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fh.write(text + "\n")
                os.replace(tmp, spec.reg_path)
            except Exception as exc:  # noqa: BLE001
                return False, f"写注册条失败: {exc}"
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

                if status == "starting":
                    alive = handle.popen.poll() is None if handle else False
                    # 快速可达判定，不做完整 urlopen（无监听端口可能被代理/TUN拖成数秒，
                    # 会卡住整个 monitor 循环）；running 后仍由复检兜底核对真健康
                    healthy = runner.quick_alive(spec, handle)
                    why = "" if healthy else "健康目标未就绪"
                    timeout = spec.health_timeout
                    elapsed = now - float(st.get("started_at") or now)
                    if healthy:
                        st["status"], st["error"] = ("running", None)
                        continue
                    if spec.mode == "detached" and handle and not alive:
                        # 点火器脚本退场 = 正常；摘句柄，继续只等健康就绪
                        self.handles.pop(name, None)
                        runner.close_handle(handle)
                        handle = None
                    if spec.mode == "managed":
                        if not alive:
                            rc = handle.popen.returncode if handle else None
                            self.handles.pop(name, None)
                            if handle:
                                runner.close_handle(handle)
                            st.update(status="failed",
                                      error=f"进程启动即退出 rc={rc}", fail_at=now)
                            continue
                    if elapsed > timeout:
                        # 关键修复点：超时必须收敛，不能像 早期内部版本 那样残留 starting
                        st.update(status="failed",
                                  error=f"健康检查超时({timeout:.0f}s){('，' + why) if why else ''}",
                                  fail_at=now)

                elif status == "failed" and spec.mode == "detached":
                    # 假失败自愈：detached 型服务被外部拉起/升级慢未就绪时，
                    # 只要健康探测通过就自动归位 running（现实中已活着就别让用户手动重启）
                    if spec.health_type in ("port", "http"):
                        alive, _w = runner.check_health(spec, None)
                        if alive:
                            st.update(status="running", started_at=now, error=None)

                elif status == "running":
                    if spec.mode == "detached":
                        # 点火器型：运行中只信健康复检；失联=被外部停止
                        if handle and handle.popen.poll() is not None:
                            self.handles.pop(name, None)
                            runner.close_handle(handle)
                        if (spec.health_type in ("port", "http")
                                and now - float(st.get("last_check") or 0) >= 5):
                            st["last_check"] = now
                            healthy, why = runner.check_health(spec, None)
                            if not healthy:
                                st.update(status="stopped", started_at=None,
                                          error=(why or "健康检查失联，进程可能已被外部停止"))
                        continue
                    if handle and handle.popen.poll() is not None:
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
                    if handle and spec.health_type in ("port", "http") and now - float(st.get("last_check") or 0) >= 5:
                        # running 态每 5 秒复检一次健康端口，端口掉线即失败提示
                        st["last_check"] = now
                        if spec.mode == "on_demand":
                            # 按需服务：复检顺带解析服务自报的 last_active，超时则空闲回收
                            healthy, why, last_active = runner.check_health_ext(spec, handle)
                            if healthy and last_active and last_active > 0 \
                                    and (now - last_active) > spec.idle_timeout:
                                threading.Thread(
                                    target=self._reap_idle, args=(name,), daemon=True).start()
                        else:
                            healthy, why = runner.check_health(spec, handle)
                        if not healthy:
                            st["error"] = why or "健康检查失败(复检)"

                elif status == "stopping":
                    began = self._pending_stop.get(name)
                    if began and now - began > runner._STOP_WAIT_SEC + 6:
                        st.update(status="stopped", started_at=None)  # 兜底收敛
