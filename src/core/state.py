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
import os
import threading
import time
from pathlib import Path

from . import audit, runner
from .linkage import link_crons
from .scanner import IDLE_MAX_SEC, IDLE_MIN_SEC, Scanner, Spec

TICK_SEC = 1.0


def _dur(t0: float) -> int:
    """距 t0 的毫秒数（登记簿 dur_ms 用）。"""
    return int((time.time() - t0) * 1000)


class Manager:
    def __init__(self, scanner: Scanner, order_path: Path | None = None,
                 cron_specs_provider=None):
        """cron_specs_provider: 可选的可调用对象，返回 {任务名: CronSpec}。

        用于把定时任务按路径归属到服务（见 core/linkage.py）。不传即无关联，
        面板该区块显示为空——单测与纯服务场景不依赖调度器。
        """
        self.scanner = scanner
        self.cron_specs_provider = cron_specs_provider
        self.order_path = Path(order_path) if order_path else None
        self.specs: dict[str, Spec] = {}
        self.handles: dict[str, runner.ProcessHandle] = {}
        # svc -> 状态字典：status/started_at/error/fail_at
        self.state: dict[str, dict] = {}
        self._order: list[str] = []          # 面板拖拽顺序（持久化在 order_path）
        self._user_stopping: set[str] = set()   # 区分"用户主动停"与"崩溃"
        self._pending_stop: dict[str, float] = {}  # stop 发起时刻，stopping 超时兜底
        self._idle_reaping: set[str] = set()    # on_demand 空闲回收进行中（防重复触发）
        self.lock = threading.RLock()
        self._load_order()
        self.refresh()

    # ------------------------------------------------------------ 顺序账本
    def _load_order(self) -> None:
        if not self.order_path:
            return
        try:
            data = json.loads(self.order_path.read_text(encoding="utf-8-sig"))
            if isinstance(data, list):
                self._order = [str(n) for n in data]
        except Exception:  # noqa: BLE001 - 缺失/损坏都退回字母序
            self._order = []

    def _save_order(self) -> None:
        if not self.order_path:
            return
        try:
            import tempfile
            self.order_path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=str(self.order_path.parent), suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(self._order, ensure_ascii=False, indent=1) + "\n")
            os.replace(tmp, self.order_path)
        except OSError:
            pass

    def _ordered_names(self) -> list[str]:
        """按拖拽顺序返回服务名；未入册的新服务按字母序追加末尾（不打乱已有顺序）。"""
        known = set(self.specs)
        picked = [n for n in self._order if n in known]
        picked += sorted(known - set(picked))
        return picked

    def set_order(self, names: list[str]) -> tuple[bool, str, str]:
        """面板拖拽排序：只接受已注册服务名（去重），未提及的按字母序追加末尾。"""
        with self.lock:
            known = set(self.specs)
            seen: set[str] = set()
            picked: list[str] = []
            for n in names:
                if isinstance(n, str) and n in known and n not in seen:
                    seen.add(n)
                    picked.append(n)
            picked += sorted(known - seen)
            self._order = picked
            self._save_order()
            return True, "ok", "已排序"

    # ------------------------------------------------------------ 发现
    def refresh(self) -> None:
        with self.lock:
            specs = self.scanner.scan()
            self.specs = specs
            for name, spec in specs.items():
                if name not in self.state:
                    if not spec.valid:
                        self.state[name] = {"status": "invalid", "error": spec.error}
                        audit.emit("spec_invalid", ok=False, svc=name, note=spec.error)
                    else:
                        self.state[name] = {
                            "status": "stopped", "started_at": None,
                            "error": None, "fail_at": None,
                        }
            for gone in [n for n in self.state if n not in specs]:
                # manifest 被删：无论进程是否存活都摘除条目
                h = self.handles.pop(gone, None)
                if h and h.popen.poll() is None:
                    runner.stop_managed(h)   # 内部 finally 会清 pid 文件
                    why = "注册条已移除，顺带收割其运行实例"
                else:
                    if h:
                        runner.close_handle(h)
                    why = "注册条已移除，条目摘除"
                audit.emit("spec_invalid", svc=gone, note=why)
                del self.state[gone]
            stale = [n for n in self._order if n not in specs]
            if stale:
                self._order = [n for n in self._order if n in specs]
                self._save_order()   # 注销服务的顺序条目一并清掉
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

    # ------------------------------------------------------------ 查询
    def snapshot(self) -> list[dict]:
        with self.lock:
            out = []
            for name in self._ordered_names():   # 面板拖拽顺序，非字母序
                spec = self.specs.get(name)
                if spec is None:
                    continue
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
                    "last_active": st.get("last_active"),
                    "port": spec.health_port if spec.health_type == "port" else None,
                    "url": spec.url,
                    "uptime_sec": uptime,
                    "autostart": spec.autostart,
                    "error": st.get("error"),
                    "cron_names": self._linked_crons().get(name, []),
                })
            return out

    def _linked_crons(self) -> dict[str, list[str]]:
        """按路径把定时任务归到服务名下（见 core/linkage.py）。

        每次 snapshot 现算：注册条可能被增删改，缓存会失真；数量级很小（几十个），
        不值得为此维护失效逻辑。
        """
        if not self.cron_specs_provider:
            return {}
        try:
            cron_specs = self.cron_specs_provider() or {}
        except Exception:  # noqa: BLE001 - 调度器异常不该拖垮面板
            return {}
        if not cron_specs:
            return {}
        return link_crons(self.specs, cron_specs)

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

    def adopt_external(self, name: str, reason: str, act: str = "adopt_external") -> None:
        """把已在外部运行的实例归位为 running（自启跳过等场景，让状态如实反映）。"""
        with self.lock:
            st = self.state.get(name)
            if st is None:
                return
            if st["status"] != "running":
                st.update(status="running", started_at=st.get("started_at") or time.time(),
                          error=None, fail_at=None)
                audit.emit(act, svc=name, note=reason)

    # ------------------------------------------------------------ 操作
    def start(self, name: str, via: str = "api") -> tuple[bool, str]:
        """拉起服务。via 只影响登记簿文案（api/autostart/cli/面板点开即启）。"""
        began = time.time()
        with self.lock:
            spec = self.specs.get(name)
            st = self.state.get(name)
            if spec is None or st is None:
                audit.emit("start", ok=False, svc=name, note=f"未知服务 {name}",
                           dur_ms=_dur(began))
                return False, f"未知服务 {name}"
            if not spec.valid:
                audit.emit("start", ok=False, svc=name, note=f"注册条无效：{spec.error}",
                           dur_ms=_dur(began))
                return False, f"注册条无效: {spec.error}"
            if not spec.enabled:
                audit.emit("start", ok=False, svc=name, note="服务已被 enabled:false 停用",
                           dur_ms=_dur(began))
                return False, "服务已被 enabled:false 停用"
            cur = st["status"]
            if cur in ("running", "starting"):
                return True, f"已在 {cur}"   # 无状态变化，不记事件
            # 防双开：冷启动（stopped/failed）前确认没有已存在的实例（外部/残留/手动），
            # 否则会双重拉起（some-service 实弹教训；process 型靠 pidfile 判定）。
            if cur in ("stopped", "failed"):
                alive, _reason = self.external_alive(name)
                if alive:
                    why = f"检测到已有实例在运行（{_reason}），已拒绝重复启动"
                    st.update(status="failed", error=why, fail_at=time.time())
                    audit.emit("reject_double", ok=False, svc=name, note=_reason,
                               dur_ms=_dur(began))
                    return False, why
            try:
                handle = runner.start_process(spec)
            except Exception as exc:  # noqa: BLE001
                st.update(status="failed", error=f"启动失败: {exc}", fail_at=time.time())
                audit.emit("spawn_fail", ok=False, svc=name, note=f"启动失败：{exc}",
                           dur_ms=_dur(began))
                return False, str(exc)
            self.handles[name] = handle
            # 发起不成一条事件：成败由 monitor 在 starting 收敛时落终态（含耗时）
            st.update(status="starting", started_at=handle.started_at, error=None,
                      fail_at=None, start_began=began, start_via=via)
            return True, "starting"

    def stop(self, name: str, act: str = "stop", reason: str = "") -> tuple[bool, str]:
        """停止服务。act/reason 只影响登记簿文案（idle_reclaim 等复用本函数）。"""
        began = time.time()
        # 阶段一（持锁）：标记 stopping 并把 handle 从登记表摘除——
        # 此后 monitor 对该服务的 starting/running 分支因 handle 缺席而不再干预
        with self.lock:
            spec = self.specs.get(name)
            st = self.state.get(name)
            handle = self.handles.pop(name, None)
            if spec is None or st is None:
                audit.emit(act, ok=False, svc=name, note=f"未知服务 {name}",
                           dur_ms=_dur(began))
                return False, f"未知服务 {name}"
            if (st["status"] in ("stopped", "failed", "invalid")) and handle is None:
                if not st["status"].startswith("invalid"):
                    st.update(status="stopped", error=None, started_at=None)
                return True, "已停止"      # 本来就没跑，不记事件
            self._user_stopping.add(name)
            self._pending_stop[name] = time.time()
            st["status"] = "stopping"
            was_pid = handle.pid if handle else None

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
        audit.emit(act, ok=ok, svc=name, dur_ms=_dur(began), pid=was_pid,
                   note=reason or ("" if ok else f"停止异常：{msg}"))
        return ok, msg

    def restart(self, name: str, via: str = "api") -> tuple[bool, str]:
        # 登记簿里表现为「stop → start」两条事件，不再单独造 restart 记录
        ok, msg = self.stop(name)
        if not ok:
            return ok, msg
        return self.start(name, via=via)

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
            self.stop(name, act="idle_reclaim",
                      reason=f"空闲超 {int(spec.idle_timeout)}s，按需服务自动回收")
        finally:
            with self.lock:
                self._idle_reaping.discard(name)

    def toggle_autostart(self, name: str, value: bool) -> tuple[bool, str]:
        with self.lock:
            spec = self.specs.get(name)
            if spec is None:
                return False, f"未知服务 {name}"
            if spec.mode == "on_demand":
                return False, "on_demand 模式按需启动，不支持设置自启"
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

    def update_config(self, name: str, patch: dict) -> tuple[bool, str, str]:
        """面板设置：改注册条的可编辑项。当前支持 `idle_timeout_sec`。

        与 Scheduler.update_config 同一套安全网：读原文备份 → 应用补丁 →
        原子写 → 用 Spec 重解析校验，判无效则**回滚原文本**并报错——绝不留
        一个坏注册条（否则服务直接变 invalid 不可用）。

        返回 (ok, code, message)；code ∈ ok / not_found / invalid / io。
        """
        with self.lock:
            spec = self.specs.get(name)
            if spec is None:
                return False, "not_found", f"未知服务 {name}"

            # 目前仅 on_demand 有可配项；常驻服务传此字段即 misuse
            if "idle_timeout_sec" in patch and spec.mode != "on_demand":
                return False, "invalid", ("idle_timeout_sec 仅 on_demand 服务可配；"
                                          f"{name} 当前为 {spec.mode}")
            if not patch:
                return False, "invalid", "没有需要修改的字段"

            try:
                old_text = spec.reg_path.read_text(encoding="utf-8-sig")
                raw = json.loads(old_text)
                if not isinstance(raw, dict):
                    raise ValueError("注册条根节点不是对象")
            except Exception as exc:  # noqa: BLE001
                return False, "io", f"读注册条失败: {exc}"

            # ---- 应用补丁（粗校验；细校验交给写后重解析）----
            if "idle_timeout_sec" in patch:
                try:
                    val = float(patch["idle_timeout_sec"])
                except (TypeError, ValueError):
                    return False, "invalid", "idle_timeout_sec 需为数字"
                # ★ 显式拒绝越界，不能让 Spec 的 clamp 悄悄吞掉：
                #   否则注册条留下 5、实际生效 60，文件与内存不一致且面板回弹无解释。
                if val < IDLE_MIN_SEC or val > IDLE_MAX_SEC:
                    return False, "invalid", (
                        f"idle_timeout_sec 需在 {int(IDLE_MIN_SEC)} ~ "
                        f"{int(IDLE_MAX_SEC)} 秒之间")
                raw["idle_timeout_sec"] = int(val)   # 落盘统一整数，避免 900.0/900 混写

            def _write(payload: dict) -> bool:
                try:
                    import os
                    import tempfile

                    fd, tmp = tempfile.mkstemp(dir=str(spec.reg_path.parent),
                                               suffix=".tmp")
                    with os.fdopen(fd, "w", encoding="utf-8") as fh:
                        fh.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
                    os.replace(tmp, spec.reg_path)
                    return True
                except Exception:  # noqa: BLE001
                    return False

            if not _write(raw):
                return False, "io", "写注册条失败"

            # ---- 写后校验：新配置必须自身合法，否则回滚 ----
            new_spec = Spec(name, spec.reg_path, spec.adapter_dir)
            if not new_spec.valid:
                _write(json.loads(old_text))   # 回滚，不留坏注册条
                return False, "invalid", (new_spec.error
                                          or "新配置不合法，已回滚")
            self.specs[name] = new_spec
            return True, "ok", "已保存"

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
                    began = st.get("start_began")
                    dur = _dur(float(began)) if began else None
                    via = str(st.get("start_via") or "api")
                    alive = handle.popen.poll() is None if handle else False
                    # 快速可达判定，不做完整 urlopen（无监听端口可能被代理/TUN拖成数秒，
                    # 会卡住整个 monitor 循环）；running 后仍由复检兜底核对真健康
                    healthy = runner.quick_alive(spec, handle)
                    why = "" if healthy else "健康目标未就绪"
                    timeout = spec.health_timeout
                    elapsed = now - float(st.get("started_at") or now)
                    if healthy:
                        st["status"], st["error"] = ("running", None)
                        st.pop("start_began", None)
                        st.pop("start_via", None)
                        audit.emit("start", svc=name, dur_ms=dur,
                                   pid=handle.pid if handle else None,
                                   note=f"{spec.mode} 健康就绪（{via}）")
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
                            st.pop("start_began", None)
                            st.pop("start_via", None)
                            audit.emit("crash", ok=False, svc=name, dur_ms=dur,
                                       note=f"进程启动即退出 rc={rc}（{via}）")
                            continue
                    if elapsed > timeout:
                        # 关键修复点：超时必须收敛，不能像 早期内部版本 那样残留 starting
                        note = (f"健康检查超时({timeout:.0f}s)"
                                f"{('，' + why) if why else ''}（{via}）")
                        st.update(status="failed", error=note, fail_at=now)
                        st.pop("start_began", None)
                        st.pop("start_via", None)
                        audit.emit("start", ok=False, svc=name, dur_ms=dur, note=note)

                elif status == "failed" and spec.mode == "detached":
                    # 假失败自愈：detached 型服务被外部拉起/升级慢未就绪时，
                    # 只要健康探测通过就自动归位 running（现实中已活着就别让用户手动重启）
                    if spec.health_type in ("port", "http"):
                        alive, _w = runner.check_health(spec, None)
                        if alive:
                            st.update(status="running", started_at=now, error=None)
                            audit.emit("health_flip", svc=name,
                                       note="假失败自愈：健康探测通过，归位 running")

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
                                audit.emit("health_flip", ok=False, svc=name,
                                           note=why or "健康失联：进程可能已被外部停止")
                        continue
                    if handle and handle.popen.poll() is not None:
                        rc = handle.popen.returncode
                        crashed = name not in self._user_stopping
                        self.handles.pop(name, None)
                        runner.close_handle(handle)
                        if crashed:
                            st.update(status="failed", error=f"进程意外退出 rc={rc}",
                                      fail_at=now)
                            audit.emit("crash", ok=False, svc=name,
                                       note=f"进程意外退出 rc={rc}")
                        else:
                            st.update(status="stopped", started_at=None, error=None)
                        continue
                    if spec.health_type in ("port", "http") and now - float(st.get("last_check") or 0) >= 5:
                        # running 态每 5 秒复检一次健康端口；不依赖 handle——
                        # 外部归位实例（handle=None）也能被持续监控
                        st["last_check"] = now
                        if spec.mode == "on_demand":
                            # 按需服务：复检顺带解析服务自报的 last_active，超时则空闲回收
                            healthy, why, last_active = runner.check_health_ext(spec, handle)
                            if last_active and last_active > 0:
                                st["last_active"] = last_active
                            if healthy and last_active and last_active > 0 \
                                    and (now - last_active) > spec.idle_timeout:
                                threading.Thread(
                                    target=self._reap_idle, args=(name,), daemon=True).start()
                        else:
                            healthy, why = runner.check_health(spec, handle)
                        if healthy:
                            st.pop("flip_noted", None)   # 恢复后允许再记一次翻转
                        if not healthy:
                            if handle is None:
                                # 外部归位实例（无句柄）：健康失联 = 已被外部停止，干净归位
                                st.update(status="stopped", started_at=None, error=None)
                                audit.emit("health_flip", ok=False, svc=name,
                                           note="外部实例已失联，归位 stopped")
                            else:
                                st["error"] = why or "健康检查失败(复检)"
                                # 状态未翻转（仍 running）：只记第一次，否则每 5 秒刷一条噪音
                                if not st.get("flip_noted"):
                                    st["flip_noted"] = True
                                    audit.emit("health_flip", ok=False, svc=name,
                                               note=why or "健康检查失败(复检)")

                elif status == "stopped":
                    # 外部/手动启动的实例：定期探测端口，活着就归位 running。
                    # 此前 stopped 无分支，手动拉起服务后面板永远停留在 stopped
                    # （只有宿主自启、或 failed 的 detached 自愈才会归位），不符"观测即真相"。
                    if now - float(st.get("last_check") or 0) >= 5:
                        st["last_check"] = now
                        alive, _reason = self.external_alive(name)
                        if alive:
                            # 外部/手动实例归位——统一走 adopt_external（含登记簿埋点）
                            self.adopt_external(name, f"检测到外部实例：{_reason}")

                elif status == "stopping":
                    began = self._pending_stop.get(name)
                    if began and now - began > runner._STOP_WAIT_SEC + 6:
                        st.update(status="stopped", started_at=None)  # 兜底收敛
