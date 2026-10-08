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
                 cron_specs_provider=None, runtime_path: Path | None = None):
        """cron_specs_provider: 可选的可调用对象，返回 {任务名: CronSpec}。

        用于把定时任务按路径归属到服务（见 core/linkage.py）。不传即无关联，
        面板该区块显示为空——单测与纯服务场景不依赖调度器。

        runtime_path: 运行态账本（data\\service_runtime.json 的家）；缺省取
        order_path 同目录的 service_runtime.json；都没有即纯内存不落盘。
        """
        self.scanner = scanner
        self.cron_specs_provider = cron_specs_provider
        self.order_path = Path(order_path) if order_path else None
        if runtime_path is not None:
            self.runtime_path: Path | None = Path(runtime_path)
        elif self.order_path is not None:
            self.runtime_path = self.order_path.parent / "service_runtime.json"
        else:
            self.runtime_path = None
        self.specs: dict[str, Spec] = {}
        self.handles: dict[str, runner.ProcessHandle] = {}
        # svc -> 状态字典：status/started_at/error/fail_at
        self.state: dict[str, dict] = {}
        self._order: list[str] = []          # 面板拖拽顺序（持久化在 order_path）
        self._runtime: dict[str, dict] = {}  # 服务名 → 运行态账面值（持久化在 runtime_path）
        self._user_stopping: set[str] = set()   # 区分"用户主动停"与"崩溃"
        self._pending_stop: dict[str, float] = {}  # stop 发起时刻，stopping 超时兜底
        self._idle_reaping: set[str] = set()    # on_demand 空闲回收进行中（防重复触发）
        self.lock = threading.RLock()
        self._load_order()
        self._load_runtime()
        # Scanner 每轮扫描向 Manager 取运行态：声明（注册条）与运行态的合并点
        self.scanner.runtime_provider = self.runtime_snapshot
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

    # ------------------------------------------------------------ 运行态账本
    def _load_runtime(self) -> None:
        """读 data\\service_runtime.json；缺失/损坏都从零开始（回落注册条或字段缺省）。"""
        if not self.runtime_path:
            return
        try:
            data = json.loads(self.runtime_path.read_text(encoding="utf-8-sig"))
            if isinstance(data, dict):
                self._runtime = {str(k): v for k, v in data.items()
                                 if isinstance(v, dict)}
        except Exception:  # noqa: BLE001
            self._runtime = {}

    def _save_runtime(self) -> bool:
        """原子写运行态账本（临时文件 + os.replace）。无 runtime_path 即纯内存，视为成功。"""
        if not self.runtime_path:
            return True
        try:
            import tempfile

            self.runtime_path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=str(self.runtime_path.parent), suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(self._runtime, ensure_ascii=False, indent=1) + "\n")
            os.replace(tmp, self.runtime_path)
            return True
        except OSError:
            return False

    def runtime_snapshot(self) -> dict[str, dict]:
        """运行态账面值快照（Scanner 每轮扫描时取用）。"""
        with self.lock:
            return {k: dict(v) for k, v in self._runtime.items()}

    def _set_runtime(self, name: str, patch: dict) -> bool:
        """只写运行态账本（白名单过滤，绝不碰注册条）。返回是否落盘成功。"""
        with self.lock:
            cur = dict(self._runtime.get(name) or {})
            cur.update({k: v for k, v in patch.items() if k in Spec.RUNTIME_KEYS})
            self._runtime[name] = cur
            return self._save_runtime()

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
            # 端口冲突检查（跨服务）：http 型也要查——从 url 解析端口，
            # 此前只认 health.type=="port" 的字段，三个 http 型服务一个都没查到
            seen: dict[int, str] = {}
            for name, spec in specs.items():
                if not spec.valid:
                    continue
                port = self._health_port(spec)
                if not port:
                    continue
                if port in seen and seen[port] != name:
                    st = self.state[name]
                    if not str(st.get("status", "")).startswith("invalid"):
                        st["error"] = (f"健康端口 {port} 与 {seen[port]} 声明冲突"
                                       "（两者不能同时运行）")
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
                    "port": self._health_port(spec),
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

    # ------------------------------------------------------------ 实例身份
    def identify(self, name: str) -> tuple[str, str, str]:
        """判定健康目标上跑的是不是**本服务**：→ (结论, 依据, 说明)。

        结论三态（不含"absent"，那是端口无监听的单独情形，见下）：

          mine    确认是本服务的实例（可安全接管/回收/停止）
          foreign 确认**不是**本服务（端口被别的程序占了，无权干涉）
          unknown 端口有人应答但认不出身份（保守：不冒充成功，也不当作外人）
          absent  端口无监听 / 无凭证（尚未运行）

        依据来源按"信息产生时机"分，优先级从高到低（后两条按需探测）：

          own-handle   本进程亲手拉起的句柄还在（最强，无需探测）
          own-pidfile  框架 pid 凭证上的 pid 仍存活（宿主重启后的接管场景）
          health-service  响应体自报的 service 字段 == 注册名（服务侧现成原料）
          declared-pidfile 注册条声明的 pidfile 存活（商业软件靠它兜底）

        **零额外进程**：只用 HTTP 探测（复用既有通道）与本地文件读取。
        历史上曾用 netstat/tasklist/powershell 反向探测，代价是每轮起子进程、
        数百毫秒、并在桌面弹出控制台窗口——故弃用。
        """
        spec = self.specs.get(name)
        if spec is None or not spec.valid:
            return "absent", "", "未注册或注册条无效"

        # 端口是否有监听——廉价 socket 预筛（毫秒级）。
        # 必须先算：下面"凭证类"判据都要用它交叉验证。
        # 为什么必须交叉：**pid 会被系统回收**。旧服务的进程早退了，但残留凭证里的
        # pid 可能已被判给别的进程（"存活" ≠ "是本服务"）。只看 pid 存活会误判 mine。
        port = self._health_port(spec)
        port_up = bool(port) and runner.quick_alive(spec, None)

        # ① 本进程亲手拉起的句柄：直接信任，无需任何探测
        h = self.handles.get(name)
        if h is not None and h.popen.poll() is None:
            return "mine", "own-handle", f"本框架拉起的实例（pid {h.pid}）"

        # ② 框架 pid 凭证 **且端口在监听**，才认定是本服务实例。
        # 宿主重启后句柄表为空，靠凭证接管；但绝不能凭一个存活的 pid 就冒充成功。
        own_pid = runner.read_pidfile(runner.pidfile_path(spec))
        if own_pid and runner.pid_alive(own_pid):
            if port_up:
                return "mine", "own-pidfile", f"框架 pid 凭证 {own_pid} 存活且端口在监听"
            # 凭证存活但端口空着：旧进程已退、pid 已被别的进程复用 → 不认
            return ("absent", "stale-pidfile",
                    f"残留 pid 凭证 {own_pid} 的端口 {port} 无监听")

        if spec.health_type == "http" and spec.health_url:
            # 端口不通就没有身份可谈，直接判 absent
            # （必须预筛——urlopen 在"连接被拒绝"时实测要 ~2s，socket 探测是毫秒级，
            #   且不会在每 5 秒的 monitor 里拖垮 API）
            if not port_up:
                return "absent", "", "健康端口无监听"
            # ③ 服务自报身份：复用既有 HTTP 通道，请求体里就有 service 字段
            reachable, payload, why = runner.probe_http(spec)
            if not reachable:
                return "absent", "", why or "健康目标不可达"
            claimed = (payload or {}).get("service")
            if isinstance(claimed, str) and claimed.strip():
                if claimed.strip() == self._service_name(spec, name):
                    return "mine", "health-service", f"响应体自报 service={claimed}"
                return ("foreign", "health-service",
                        f"端口 {port} 上的程序自报 service={claimed}，非本服务")
            # 无 service 字段 → 身份不确认。按三态之"无字段退回旧行为"：
            #   注册条若声明了 pidfile，仍可继续核（④）；
            #   否则无据可依，保守判 unknown（不冒充成功，也不当作外人）
            if not spec.pidfile:
                return ("unknown", "no-identity-field",
                        f"端口 {port} 有应答但未自报 service，身份不确认")
        elif spec.health_type == "port":
            if not port_up:
                return "absent", "", "端口无监听"

        # ④ 注册条声明的 pidfile（外部/商业软件兜底）——同样要求端口在监听
        #    （商业软件自带的 pidfile 也可能过期残留，pid 同样可能被回收）
        decl_pid = self._declared_pid(spec)
        if decl_pid and runner.pid_alive(decl_pid) and port_up:
            return "mine", "declared-pidfile", f"注册条 pidfile {decl_pid} 存活且端口在监听"

        if spec.health_type == "process":
            return "absent", "", "进程型且无存活凭证"
        return ("unknown", "", "有实例应答但无任何身份凭证可核") \
            if port else ("absent", "", "未运行")

    @staticmethod
    def _service_name(spec: Spec, name: str) -> str:
        """身份名：默认用注册名；注册条可用 identity.service 显式覆盖。"""
        ident = spec.raw.get("identity") if isinstance(spec.raw, dict) else None
        if isinstance(ident, dict) and ident.get("service"):
            return str(ident["service"]).strip()
        return name

    def _health_port(self, spec: Spec) -> int | None:
        """注册条声明的健康端口：port 型读字段，http 型从 url 解析。"""
        if spec.health_type == "port":
            return spec.health_port
        if spec.health_type == "http":
            _, port = runner.parse_http_host_port(spec.health_url or "")
            return port
        return None

    def _declared_pid(self, spec: Spec) -> int | None:
        """读注册条声明的 pidfile（支持相对服务目录与绝对路径两种写法）。"""
        if not spec.pidfile:
            return None
        p = Path(spec.pidfile)
        if not p.is_absolute():
            p = spec.path / p
        return runner.read_pidfile(p)

    # ------------------------------------------------------------ 外部运行判定
    def external_alive(self, name: str) -> tuple[bool, str]:
        """健康目标上是否已有**本服务**的实例在跑（超出框架进程表的）。

        判据已从"端口有人应答"升级为"按身份确认是 mine"——端口上有别人的程序
        应答不算我们的实例（否则会把陌生服务误判成自家孤儿，进而 adopt 归位、
        空闲回收，对别人的进程做无权的操作）。

        保守取向：只有 verdict==mine 才返回 True。unknown 时宁可返回 False
        （启动时可能重复拉起，但由服务自身的端口占用兜底拒绝），也不误停别人的进程。
        """
        verdict, via, reason = self.identify(name)
        if verdict == "mine":
            return True, f"{reason}（{via}）"
        if verdict == "foreign":
            return False, reason
        return False, reason or "健康目标无本服务实例"

    # ------------------------------------------------------------ 启动接管
    def reconcile(self) -> dict[str, str]:
        """宿主启动时按身份收敛残留实例，消除"重启即孤儿"。

        宿主重启后句柄表是空的。v0.11 只问"端口有人应答吗"，答不出"是不是我的"，
        于是把孤儿一律 adopt 成外部实例归位 running——既停不掉（无句柄）、也回收
        不了（判据认不出），线上表现为"空闲 0:00 却不回收"且每轮刷两条登记簿。
        本方法按 identify() 的四态分别处置：

          mine    → 归位 running（凭证已确认是我的，后续可正常 stop/回收）
          foreign → 标 conflict，如实告知"被谁占了"，不冒充
          unknown → 保持原状并记 identity_unknown，不接管不冒充
          absent  → 清掉过期 pid 凭证，保持 stopped

        返回 {服务名: 处置结果}，供宿主启动日志与登记簿留痕。
        """
        out: dict[str, str] = {}
        with self.lock:
            self.refresh()
            for name, spec in list(self.specs.items()):
                st = self.state.get(name)
                if st is None or not spec.valid or not spec.enabled:
                    continue
                verdict, via, reason = self.identify(name)
                if verdict == "mine":
                    self.adopt_external(name, f"启动接管：{reason}", via=via)
                    out[name] = f"归位（{via}）"
                elif verdict == "foreign":
                    st.update(status="conflict", error=reason, fail_at=time.time())
                    audit.emit("identity_mismatch", ok=False, svc=name, note=reason)
                    out[name] = "端口被他人占用"
                elif verdict == "unknown":
                    st["error"] = reason
                    audit.emit("identity_unknown", svc=name, note=reason)
                    out[name] = "身份不确认"
                else:
                    # absent：清掉可能残留的 pid 凭证，避免下次误认
                    pf = runner.pidfile_path(spec)
                    if runner.read_pidfile(pf) is not None:
                        try:
                            pf.unlink(missing_ok=True)
                            audit.emit("pidfile_stale", svc=name,
                                       note="端口无监听，清理残留 pid 凭证")
                        except OSError:
                            pass
                    out[name] = "未运行"
        return out

    def adopt_external(self, name: str, reason: str, act: str = "adopt_external",
                       via: str = "") -> None:
        """把已在外部运行的实例归位为 running（自启跳过等场景，让状态如实反映）。

        调用前应已由 identify() 确认是本服务的实例（verdict==mine）；本函数
        只做状态归位与埋点，不再自行判定——判定与归位分离，避免"认错还照做"。
        """
        with self.lock:
            st = self.state.get(name)
            if st is None:
                return
            if st["status"] != "running":
                st.update(status="running", started_at=st.get("started_at") or time.time(),
                          error=None, fail_at=None)
                suffix = f"（{via}）" if via else ""
                audit.emit(act, svc=name, note=reason + suffix)

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
            # 防双开：冷启动前的确认。判据已从"端口有人应答"升级为"端口上是**本服务**
            # 的实例"——确认是我的就归位（不重复拉起，也不算失败）；是外人占着则
            # 明确标 conflict 并拒绝，不把别人的服务当自己拉起，也不抹成"启动失败"。
            if cur in ("stopped", "failed", "conflict"):
                verdict, via, reason = self.identify(name)
                if verdict == "mine":
                    self.adopt_external(name, f"启动前确认为本服务实例：{reason}",
                                        act="adopt_external", via=via)
                    return True, "已在运行（实例归位）"
                if verdict == "foreign":
                    why = f"健康目标被占用：{reason}"
                    st.update(status="conflict", error=why, fail_at=time.time())
                    audit.emit("reject_double", ok=False, svc=name, note=reason,
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
            # 安全闸：端口被外人占用（conflict）且我方无句柄时，只清状态、绝不收割。
            # 否则 stop_script（如 POST /api/shutdown）会打到陌生程序上——那是别人的服务，
            # 框架无权干涉。用户点"清除"的语义就是"把这个红点清掉"，不是杀别人。
            if handle is None and st["status"] == "conflict":
                verdict, _via, why = self.identify(name)
                if verdict != "mine":
                    st.update(status="stopped", error=None, started_at=None)
                    return True, f"端口占用方非本服务，仅清除告警（{why}）"
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

    def toggle_autostart(self, name: str, value: bool) -> tuple[bool, str]:
        """面板自启开关：只改运行态账本（data\\service_runtime.json），注册条一个字节不动。"""
        with self.lock:
            spec = self.specs.get(name)
            if spec is None:
                return False, f"未知服务 {name}"
            if spec.mode == "on_demand":
                return False, "on_demand 模式按需启动，不支持设置自启"
            value = bool(value)
            if not self._set_runtime(name, {"autostart": value}):
                return False, "写运行态账本失败"
            spec.autostart = value
            return True, "ok"

    def update_config(self, name: str, patch: dict) -> tuple[bool, str, str]:
        """面板设置：改服务的可编辑项。当前支持 `idle_timeout_sec`（运行态）。

        只写 data\\service_runtime.json，注册条一个字节不动。安全网保留：范围校验
        → 记账本 → 用新运行态重解析 Spec 校验，判无效则**回滚账本**并报错——绝不
        留一个坏配置（否则服务直接变 invalid 不可用）。

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
            patch = {k: v for k, v in patch.items() if k in Spec.RUNTIME_KEYS}
            if not patch:
                return False, "invalid", "没有需要修改的字段"

            if "idle_timeout_sec" in patch:
                try:
                    val = float(patch["idle_timeout_sec"])
                except (TypeError, ValueError):
                    return False, "invalid", "idle_timeout_sec 需为数字"
                # ★ 显式拒绝越界，不能让 Spec 的 clamp 悄悄吞掉：
                #   否则账本留下 5、实际生效 60，账本与内存不一致且面板回弹无解释。
                if val < IDLE_MIN_SEC or val > IDLE_MAX_SEC:
                    return False, "invalid", (
                        f"idle_timeout_sec 需在 {int(IDLE_MIN_SEC)} ~ "
                        f"{int(IDLE_MAX_SEC)} 秒之间")
                patch["idle_timeout_sec"] = int(val)   # 落盘统一整数，避免 900.0/900 混写

            old_runtime = dict(self._runtime.get(name) or {})
            if not self._set_runtime(name, patch):
                return False, "io", "写运行态账本失败"

            # ---- 写后校验：新运行态必须让注册条仍然合法，否则回滚账本 ----
            new_spec = Spec(name, spec.reg_path, spec.adapter_dir,
                            runtime=self._runtime.get(name))
            if not new_spec.valid:
                self._runtime[name] = old_runtime   # 回滚，不留坏配置
                self._save_runtime()
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
                        # 点火器型：运行中按**身份**复检（点火器早已退场，句柄不可作数）
                        if handle and handle.popen.poll() is not None:
                            self.handles.pop(name, None)
                            runner.close_handle(handle)
                        if (spec.health_type in ("port", "http")
                                and now - float(st.get("last_check") or 0) >= 5):
                            st["last_check"] = now
                            verdict, _via, why = self.identify(name)
                            if verdict == "foreign":
                                # 端口被别的程序接手：不再冒充运行中
                                st.update(status="conflict", error=why, fail_at=now)
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
                            # mine：一切正常，不动作
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
                                # 外部归位实例（无句柄）：再核一次身份——
                                #   仍是我的 → 真被我之外的因素停了，干净归位 stopped；
                                #   变成外人 → 端口被接手，标 conflict（不冒充）；
                                #   答不上来 → 保守记 error，不轻易抹成 stopped
                                verdict, _via, iwhy = self.identify(name)
                                if verdict == "foreign":
                                    st.update(status="conflict", error=iwhy, fail_at=now)
                                    audit.emit("identity_mismatch", ok=False,
                                               svc=name, note=iwhy)
                                elif verdict == "mine":
                                    st.update(status="stopped", started_at=None, error=None)
                                    audit.emit("health_flip", ok=False, svc=name,
                                               note="本服务实例已失联，归位 stopped")
                                else:
                                    st["error"] = iwhy or why or "健康检查失败(复检)"
                            else:
                                st["error"] = why or "健康检查失败(复检)"
                                # 状态未翻转（仍 running）：只记第一次，否则每 5 秒刷一条噪音
                                if not st.get("flip_noted"):
                                    st["flip_noted"] = True
                                    audit.emit("health_flip", ok=False, svc=name,
                                               note=why or "健康检查失败(复检)")

                elif status == "stopped":
                    # 外部/手动启动的实例：定期按**身份**探测，确认是本服务才归位 running。
                    # 此前 stopped 无分支，手动拉起服务后面板永远停留在 stopped；
                    # 且判据只是"端口有人应答"，会把占着同端口的别人的程序认成自家实例。
                    if now - float(st.get("last_check") or 0) >= 5:
                        st["last_check"] = now
                        verdict, via, reason = self.identify(name)
                        if verdict == "mine":
                            # 本服务实例（外部/手动拉起的）归位——走 adopt_external（含埋点）
                            self.adopt_external(
                                name, f"检测到本服务实例：{reason}", via=via)
                        elif verdict == "foreign":
                            # 端口被别的程序占着：如实标注，不冒充、也不无权停它
                            st.update(status="conflict", error=reason, fail_at=now)
                            audit.emit("identity_mismatch", ok=False, svc=name, note=reason)
                        # unknown / absent：保持 stopped，不动

                elif status == "stopping":
                    began = self._pending_stop.get(name)
                    if began and now - began > runner._STOP_WAIT_SEC + 6:
                        st.update(status="stopped", started_at=None)  # 兜底收敛
