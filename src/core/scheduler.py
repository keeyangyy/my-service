"""my-service · core/scheduler.py — 定时任务调度器

与常驻服务（managed/detached/on_demand）并行的第二个物种：一次性脚本任务。
注册 = 在 reg\\cron\\<任务名>.json 放一条注册条（reg 的 glob 不递归，
与服务注册天然隔离）。无常驻进程、无健康检查、无状态机——只有"到点跑一次"。

调度语义：
- daily    {"type": "daily", "at": "HH:MM"}      每天 fixed 时刻（本地时间）
- interval {"type": "interval", "every_sec": N}  固定间隔循环（首跑 = 见到它起算一个周期）
- 防重叠：上一轮没跑完，本轮到点不触发；跑完后 next_run 若已过期，
  走统一的"错过"处理，绝不堆积连发
- 错过（宿主关闭/睡眠/执行超长导致，判定阈 GRACE=60s）：
  catch_up=true → 立即补跑一次；否则登记 cron_skip 并顺延到下一个合法时刻
- 状态持久化 data\\cron_state.json（原子写）：last_run/last_ok/last_rc/
  last_dur_ms/next_run，宿主重启后按此恢复计划

日志边界（与服务同一条铁律）：脚本输出一律 DEVNULL，任务日志由任务自己写；
框架只把"何时触发了、成没成、耗时、rc"记进登记簿（cron_run/cron_skip）。
"""
from __future__ import annotations

import contextlib
import json
import os
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from . import audit, runner

GRACE_SEC = 60.0          # 到点宽限：超过它还没轮上执行即判"错过"
TICK_SEC = 1.0


def _dur(t0: float) -> int:
    return int((time.time() - t0) * 1000)


class CronSpec:
    """单条定时任务注册的解析结果。valid=False 时 error 携带原因。"""

    # 用户设置字段：生效值来自注册条 settings 组（旧的 cron_runtime.json 账本已废弃）
    SETTINGS_KEYS = ("state", "catch_up", "run_timeout_sec", "order")

    def __init__(self, name: str, reg_path: Path, runtime: dict | None = None):
        self._init_defaults(name, reg_path)
        view = self._load(runtime)
        if view is None:
            return
        problems: list[str] = []
        problems += self._apply_declaration(view, name)
        problems += self._apply_schedule(view)
        problems += self._check_entry()
        if problems:
            self.valid = False
            self.error = "；".join(problems)

    def _init_defaults(self, name: str, reg_path: Path) -> None:
        self.folder = name                        # 任务名 = 注册文件名（去 .json）
        self.reg_path = reg_path                  # reg\tasks\<name>.json（只读声明）
        self.raw: dict = {}                       # 注册条原文（声明 + 默认计划）
        self.runtime: dict = {}                   # 本条运行态账面值（生效值来源）
        self.valid: bool = True
        self.error: str | None = None
        self.path: Path = Path('.')
        self.state: str = "disabled"             # active | disabled | archived（task 默认不启用）
        self.schedule_active: bool = True        # weekly days 为空 → False（不调度）
        self.display: str = name
        self.description: str = ""
        self.entry: str = ""
        self.args: list[str] = []
        self.cwd: str = "."
        self.env: dict[str, str] = {}
        self.schedule_type: str = ""              # daily | weekly | interval
        self.schedule_times: list[tuple[int, int]] = []
        self.schedule_days: list[int] = []        # weekly：weekday 0=周一 … 6=周日
        self.every_sec: float = 0.0
        self.timeout_sec: float = 300.0
        self.catch_up: bool = False
        self.order: float | None = None

    @property
    def enabled(self) -> bool:
        """兼容既有调用：state != disabled。"""
        return self.state != "disabled"

    @property
    def archived(self) -> bool:
        return self.state == "archived"

    def _load(self, runtime: dict | None) -> dict | None:
        """读注册条并与运行态账本合并 → view；解析失败返回 None。"""
        try:
            data = json.loads(self.reg_path.read_text(encoding="utf-8-sig"))
            if not isinstance(data, dict):
                raise ValueError("注册条根节点必须是 JSON 对象")
            self.raw = data
        except Exception as exc:  # noqa: BLE001 - 坏注册条一律变 invalid
            self.valid = False
            self.error = f"注册条解析失败: {exc}"
            return None
        # 账本值覆盖注册条同名键（后者只作回落）
        self.runtime = {k: runtime[k] for k in self.SETTINGS_KEYS
                        if isinstance(runtime, dict) and k in runtime}
        return {**data, **self.runtime}

    def _apply_declaration(self, view: dict, name: str) -> list[str]:
        problems: list[str] = []
        legacy = [k for k in ("entry", "args", "cwd", "enabled", "catch_up",
                              "timeout_sec", "runtime") if k in view]
        if legacy:
            problems.append(f"旧版字段 {legacy} 已废弃，请改为 start / settings 分组")

        raw_path = view.get("path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            problems.append("缺少 path 字段（脚本所在目录）")
        else:
            cand = Path(raw_path.strip())
            if not cand.is_absolute():
                cand = (self.reg_path.parent / cand).resolve()
            self.path = cand.resolve()
            if not self.path.is_dir():
                problems.append(f"path 不是存在的目录: {self.path}")

        start = view.get("start") if isinstance(view.get("start"), dict) else {}
        settings = view.get("settings") if isinstance(view.get("settings"), dict) else {}
        state = str(settings.get("state") or "disabled").lower()
        if state not in ("active", "disabled", "archived"):
            problems.append(
                f"settings.state 仅支持 active/disabled/archived，得到 {state!r}")
        self.state = state
        self.display = str(view.get("display", name))
        self.description = str(view.get("description", ""))
        self.entry = str(start.get("entry", "")).strip()
        args = start.get("args", [])
        self.args = [str(a) for a in args] if isinstance(args, list) else []
        self.cwd = str(start.get("cwd", "."))
        self.catch_up = bool(settings.get("catch_up", False))
        if "order" in settings:
            try:
                self.order = float(settings["order"])
            except (TypeError, ValueError):
                self.order = None
        try:
            self.timeout_sec = float(settings.get("run_timeout_sec", 300.0))
        except (TypeError, ValueError):
            problems.append("run_timeout_sec 不是数字")
        self.timeout_sec = max(5.0, min(self.timeout_sec, 86400.0))
        if isinstance(start.get("env"), dict):
            self.env = {str(k): str(v) for k, v in start["env"].items()}
        return problems

    def _apply_schedule(self, view: dict) -> list[str]:
        sched = view.get("schedule")
        if sched is None:
            sched = {}                      # 缺省：daily 09:00
        if not isinstance(sched, dict):
            return ["schedule 需为对象"]
        stype = str(sched.get("type") or "daily").lower()
        if stype == "daily":
            self.schedule_type = "daily"
            return self._apply_daily(sched)
        if stype == "weekly":
            self.schedule_type = "weekly"
            return self._apply_weekly(sched)
        if stype == "interval":
            self.schedule_type = "interval"
            return self._apply_interval(sched)
        return [f"schedule.type 仅支持 daily/weekly/interval，得到 {stype!r}"]

    def _apply_daily(self, sched: dict) -> list[str]:
        return self._apply_times(sched, "daily")

    def _apply_weekly(self, sched: dict) -> list[str]:
        raw_days = sched.get("days")
        if not isinstance(raw_days, list):
            raw_days = []
        days: list[int] = []
        bad: list[object] = []
        for item in raw_days:
            try:
                d = int(item)
            except (TypeError, ValueError):
                bad.append(item)
                continue
            if not (1 <= d <= 7):
                bad.append(item)
                continue
            days.append(d - 1)          # 对外 1~7，内部 weekday 0~6
        if bad:
            return [f"weekly 的 days 含非法星期（需 1~7）: {bad}"]
        self.schedule_days = sorted(set(days))
        # 空 days = 不调度（valid，但不会执行）
        self.schedule_active = bool(self.schedule_days)
        return self._apply_times(sched, "weekly")

    def _apply_times(self, sched: dict, label: str) -> list[str]:
        raw_at = sched.get("at")
        raw_at = [raw_at] if isinstance(raw_at, str) else raw_at
        if not isinstance(raw_at, list) or not raw_at:
            raw_at = ["09:00"]
        times: list[tuple[int, int]] = []
        bad: list[str] = []
        for item in raw_at:
            s = str(item).strip()
            try:
                hh, mm = s.split(":")
                t = (int(hh), int(mm))
                if not (0 <= t[0] <= 23 and 0 <= t[1] <= 59):
                    raise ValueError
                times.append(t)
            except (ValueError, TypeError):
                bad.append(s)
        if bad:
            return [f"{label} 的 at 含非法时刻（需 HH:MM）: {bad}"]
        self.schedule_times = sorted(set(times))
        if not self.schedule_times:
            return [f"{label} 的 at 不能为空"]
        return []

    def _apply_interval(self, sched: dict) -> list[str]:
        try:
            self.every_sec = float(sched.get("every_sec", 3600))
        except (TypeError, ValueError):
            self.every_sec = 3600
        if self.every_sec < 10:
            return ["interval 的 every_sec 需 >= 10 秒"]
        self.every_sec = min(self.every_sec, 604800.0)  # ≤ 7 天
        return []

    def _check_entry(self) -> list[str]:
        if not self.entry:
            return ["缺少 entry 字段"]
        if self.entry_abs and not self.entry_abs.is_file():
            return [f"入口文件不存在: {self.entry_abs}"]
        return []

    def _resolve(self, rel: str) -> Path:
        return (self.path / rel).resolve()

    @property
    def workdir(self) -> Path:
        return (self.path / self.cwd).resolve()

    @property
    def entry_abs(self) -> Path | None:
        if not self.entry:
            return None
        return self._resolve(self.entry)

    @property
    def schedule_desc(self) -> str:
        """一句话摘要（面板/登记簿用）。"""
        if self.schedule_type == "daily" and self.schedule_times:
            if len(self.schedule_times) == 1:
                h, m = self.schedule_times[0]
                return f"每天 {h:02d}:{m:02d}"
            return "每天 " + ", ".join(f"{h:02d}:{m:02d}"
                                       for h, m in self.schedule_times)
        if self.schedule_type == "weekly" and not self.schedule_days:
            return "每周（未选择星期，不执行）"
        if (self.schedule_type == "weekly" and self.schedule_days
                and self.schedule_times):
            days = "、".join(_WEEKDAY_NAMES[d] for d in self.schedule_days)
            times = ", ".join(f"{h:02d}:{m:02d}" for h, m in self.schedule_times)
            return f"每周{days} {times}"
        if self.schedule_type == "interval" and self.every_sec >= 5:
            if self.every_sec % 3600 == 0:
                return f"每 {int(self.every_sec // 3600)} 小时"
            if self.every_sec % 60 == 0:
                return f"每 {int(self.every_sec // 60)} 分钟"
            return f"每 {int(self.every_sec)} 秒"
        return "未知"


def _next_daily(times: list[tuple[int, int]], after: float) -> float:
    """after 之后的下一时刻：取 times（升序）中未来的最近一个；全过则明天最早。"""
    dt = datetime.fromtimestamp(after)
    for h, m in sorted(times):
        cand = dt.replace(hour=h, minute=m, second=0, microsecond=0)
        if cand.timestamp() > after:
            return cand.timestamp()
    h, m = min(times)
    return (dt + timedelta(days=1)).replace(
        hour=h, minute=m, second=0, microsecond=0).timestamp()


_WEEKDAY_NAMES = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


def _next_weekly(days: list[int], times: list[tuple[int, int]],
                 after: float) -> float:
    """after 之后下一个匹配（星期 + 时刻）；days 用 weekday 0=周一 … 6=周日。"""
    base = datetime.fromtimestamp(after)
    for offset in range(0, 8):
        day = base + timedelta(days=offset)
        if day.weekday() not in days:
            continue
        for h, m in sorted(times):
            cand = day.replace(hour=h, minute=m, second=0, microsecond=0)
            if cand.timestamp() > after:
                return cand.timestamp()
    # 兜底（days 非空时理论上不可达）：排到 7 天后最早时刻
    h, m = min(times)
    return (base + timedelta(days=7)).replace(
        hour=h, minute=m, second=0, microsecond=0).timestamp()


class Scheduler:
    """定时任务编排器：扫描 reg\\tasks\\ + 按 tick_sec 调度 + 到点开线程执行。"""

    def __init__(self, cron_dir: Path, state_path: Path, order_path: Path | None = None,
                 runtime_path: Path | None = None, tick_sec: float = 10.0):
        """tick_sec: 调度检查间隔（秒，默认 10）。"""
        self.cron_dir = Path(cron_dir)
        self.state_path = Path(state_path)
        # order_path / runtime_path 仅为兼容旧调用保留：顺序与运行态已并入注册条
        self.order_path = None
        self.runtime_path = None
        self.tick_sec = float(tick_sec) if tick_sec else 10.0
        self.specs: dict[str, CronSpec] = {}
        self.persist: dict[str, dict] = {}   # name -> last_run/last_ok/.../next_run
        self._order: list[str] = []          # 面板拖拽顺序（持久化在 order_path）
        self._runtime: dict[str, dict] = {}  # 运行态账面值（持久化在 runtime_path）
        self.running: set[str] = set()
        self.lock = threading.RLock()
        self._load_state()
        self._load_order()
        self._load_runtime()

    # ------------------------------------------------------------ 顺序（runtime.order）
    def _load_order(self) -> None:
        """顺序已并入注册条 runtime.order，这里仅保留调用点。"""
        self._order = []

    def _save_order(self) -> None:
        for i, name in enumerate(self._order, 1):
            spec = self.specs.get(name)
            if spec is None or spec.order == float(i):
                continue
            self._set_settings(name, {"order": i})

    def _ordered_names(self) -> list[str]:
        """按 runtime.order 排序；未设 order 的按字母序追加末尾。"""
        return sorted(
            self.specs,
            key=lambda n: (self.specs[n].order
                           if self.specs[n].order is not None else 1e9, n),
        )

    def set_order(self, names: list[str]) -> tuple[bool, str, str]:
        """面板拖拽排序：只接受已知任务名（去重），未提及的按字母序追加末尾。"""
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

    # ------------------------------------------------------------ 持久化
    def _load_state(self) -> None:
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8-sig"))
            if isinstance(data, dict):
                self.persist = data
        except Exception:  # noqa: BLE001 - 文件缺失/损坏都从零开始
            self.persist = {}

    def _save_state(self) -> None:
        """原子写（临时文件 + os.replace），任何失败静默——状态账本不得拖垮调度。"""
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = __import__("tempfile").mkstemp(
                dir=str(self.state_path.parent), suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(self.persist, ensure_ascii=False, indent=1))
            os.replace(tmp, self.state_path)
        except OSError:
            pass

    # ------------------------------------------------------------ 运行态（写回注册条）
    def _load_runtime(self) -> None:
        """运行态已并入注册条，无独立账本。"""
        self._runtime = {}

    def _save_runtime(self) -> bool:
        return True

    def runtime_snapshot(self) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for name, spec in self.specs.items():
            out[name] = {
                "state": spec.state,
                "catch_up": spec.catch_up,
                "run_timeout_sec": spec.timeout_sec,
                "order": spec.order,
                "schedule_active": spec.schedule_active,
            }
        return out

    def _write_reg(self, name: str, mutate) -> bool:
        """读注册条 → mutate(data) → 原子写回 → 重解析。"""
        spec = self.specs.get(name)
        if spec is None:
            return False
        try:
            data = json.loads(spec.reg_path.read_text(encoding="utf-8-sig"))
            mutate(data)
            fd, tmp = __import__("tempfile").mkstemp(
                dir=str(spec.reg_path.parent), suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
            os.replace(tmp, spec.reg_path)
        except OSError:
            return False
        new_spec = CronSpec(name, spec.reg_path)
        if new_spec.valid:
            self.specs[name] = new_spec
        return True

    def _set_settings(self, name: str, patch: dict) -> bool:
        clean = {k: v for k, v in patch.items() if k in CronSpec.SETTINGS_KEYS}
        if not clean:
            return True

        def mutate(data: dict) -> None:
            st = data.get("settings")
            if not isinstance(st, dict):
                st = {}
            st.update(clean)
            data["settings"] = st

        return self._write_reg(name, mutate)

    def _set_schedule(self, name: str, sched: dict) -> bool:
        def mutate(data: dict) -> None:
            data["schedule"] = sched

        return self._write_reg(name, mutate)

    # ------------------------------------------------------------ 发现
    def scan(self) -> None:
        specs: dict[str, CronSpec] = {}
        if self.cron_dir.is_dir():
            for f in sorted(self.cron_dir.glob("*.json")):
                spec = CronSpec(f.stem, f)
                specs[spec.folder] = spec
        with self.lock:
            gone = [n for n in self.specs if n not in specs]
            self.specs = specs
            stale = [n for n in self._order if n not in specs]
            for name in gone:
                self.persist.pop(name, None)   # 注册条已删：账本条目一并清
                self.running.discard(name)
                self._save_state()
            if stale:
                self._order = [n for n in self._order if n in specs]
                self._save_order()             # 同步清掉已注销任务的顺序条目

    # ------------------------------------------------------------ 查询
    def snapshot(self) -> list[dict]:
        with self.lock:
            out = []
            for name in self._ordered_names():   # 面板拖拽顺序，非字母序
                spec = self.specs.get(name)
                if spec is None:
                    continue
                p = self.persist.get(name, {})
                out.append({
                    "name": name,
                    "display": spec.display,
                    "description": spec.description,
                    "state": spec.state,
                    "schedule_active": spec.schedule_active,
                    "valid": spec.valid,
                    "error": spec.error,
                    "schedule": spec.schedule_desc,
                    # 编辑控件用的原始参数（schedule_desc 仅供展示）
                    "schedule_type": spec.schedule_type,
                    "at": ([f"{h:02d}:{m:02d}" for h, m in spec.schedule_times]
                           if spec.schedule_times else None),
                    "days": ([d + 1 for d in spec.schedule_days]
                             if spec.schedule_days else None),
                    "every_sec": spec.every_sec if spec.every_sec >= 5 else None,
                    "timeout_sec": spec.timeout_sec,
                    "catch_up": spec.catch_up,
                    "running": name in self.running,
                    "next_run": p.get("next_run"),
                    "last_run": p.get("last_run"),
                    "last_ok": p.get("last_ok"),
                    "last_rc": p.get("last_rc"),
                    "last_dur_ms": p.get("last_dur_ms"),
                })
            return out

    # ------------------------------------------------------------ 调度
    def loop(self) -> None:
        """调度主循环——与 monitor_loop 并列的第二个永动线程。"""
        while True:
            with contextlib.suppress(Exception):   # 调度线程永不退出
                self.tick()
            time.sleep(self.tick_sec)

    def _next_for(self, spec: CronSpec, after: float) -> float | None:
        """按类型算下一个计划时刻；interval 返回 None（由调用方按周期处理）。"""
        if spec.schedule_type == "daily" and spec.schedule_times:
            return _next_daily(spec.schedule_times, after)
        if (spec.schedule_type == "weekly" and spec.schedule_days
                and spec.schedule_times):
            return _next_weekly(spec.schedule_days, spec.schedule_times, after)
        return None

    def _init_next_run(self, spec: CronSpec, now: float) -> float:
        """新任务的首次计划：daily/weekly 排到下一个时刻；interval 起算一个周期。"""
        nxt = self._next_for(spec, now)
        return nxt if nxt is not None else now + spec.every_sec

    def _defer_after_miss(self, spec: CronSpec, now: float) -> float:
        """错过且不补跑时的顺延目标：绝不回头补，只往前排。"""
        nxt = self._next_for(spec, now)
        return nxt if nxt is not None else now + spec.every_sec

    def _defer_after_run(self, spec: CronSpec, last_run: float, now: float) -> float:
        """执行完毕后的下一次计划：interval 按上次起算（已过期则从现在重新起算）。"""
        nxt = self._next_for(spec, now)
        if nxt is not None:
            return nxt
        cand = last_run + spec.every_sec
        if cand <= now + 1:
            cand = now + spec.every_sec
        return cand

    def tick(self) -> None:
        self.scan()
        now = time.time()
        with self.lock:
            for name, spec in list(self.specs.items()):
                if (not spec.valid or not spec.enabled
                        or not spec.schedule_active or name in self.running):
                    continue
                p = self.persist.setdefault(name, {})
                nxt = p.get("next_run")
                if nxt is None:
                    p["next_run"] = self._init_next_run(spec, now)
                    self._save_state()
                    continue
                if now < nxt:
                    continue
                if now > nxt + GRACE_SEC and not spec.catch_up:
                    # 错过且不补跑：登记一次，顺延到未来（防连发）
                    p["next_run"] = self._defer_after_miss(spec, now)
                    self._save_state()
                    audit.emit("cron_skip", ok=True, svc=name,
                               note="错过计划时刻，catch_up=false 顺延至下一周期")
                    continue
                # 到点（或 catch_up 补跑）：via 区分"按计划"与"错过补跑"，
                # 便于登记簿事后区分两种来源
                via = "catch_up" if now > nxt + GRACE_SEC else "schedule"
                threading.Thread(target=self._execute, args=(name, spec, via),
                                 daemon=True, name=f"cron-{name}").start()

    # ------------------------------------------------------------ 执行
    def _execute(self, name: str, spec: CronSpec, via: str) -> None:
        """到点/手动执行一轮。执行完统一重排 next_run 并持久化。"""
        began = time.time()
        with self.lock:
            if name in self.running:
                return
            self.running.add(name)
            self.persist.setdefault(name, {})
        try:
            entry = spec.entry_abs
            if entry is None:
                rc, dur_ms, err = None, 0, "入口为空"
            else:
                rc, dur_ms, err = runner.run_once(
                    entry, spec.args, spec.workdir, spec.env, spec.timeout_sec)
            ok = (err is None and rc == 0)
            now = time.time()
            with self.lock:
                p2 = self.persist.setdefault(name, {})
                p2.update(last_run=began, last_ok=ok, last_rc=rc,
                          last_dur_ms=dur_ms)
                p2["next_run"] = self._defer_after_run(spec, began, now)
                self._save_state()
            # note 只记结果与非常规来源（定时触发是常态，不加前缀；
            # 手动/补跑用中文标注，调度规则在卡片上已有，不进事件行）
            via_note = {"manual": "手动触发，", "cli": "手动触发，",
                        "api": "手动触发，", "catch_up": "错过补跑，"}.get(via, "")
            if err is not None:
                note = f"{via_note}启动失败: {err}"
            elif rc is None:
                note = f"{via_note}超时({spec.timeout_sec:.0f}s)已终止"
            else:
                note = f"{via_note}rc={rc}"
            audit.emit("cron_run", ok=ok, svc=name, dur_ms=dur_ms, note=note)
        finally:
            with self.lock:
                self.running.discard(name)

    def update_config(self, name: str, patch: dict) -> tuple[bool, str, str]:
        """面板行内编辑：schedule / run_timeout_sec / catch_up / enabled，写回注册条。

        写后重解析校验，判无效则回滚，绝不留坏配置。
        schedule 变更时重算 next_run（否则面板显示新时间、实际仍按旧时间触发）。

        返回 (ok, code, message)；code 同 run_now 的映射约定。
        """
        with self.lock:
            spec = self.specs.get(name)
            if spec is None:
                return False, "not_found", f"未知定时任务 {name}"
            runtime_patch, schedule_patch, error = self._parse_patch(patch)
            if error:
                return False, "invalid", error
            if not runtime_patch and schedule_patch is None:
                return False, "invalid", "没有需要修改的字段"
            return self._commit_patch(name, spec, runtime_patch, schedule_patch)

    def _parse_patch(self, patch: dict) -> tuple[dict, dict | None, str]:
        """把外部 patch 归一化 → (runtime_patch, schedule_patch, 错误消息)。"""
        runtime_patch: dict = {}
        if "enabled" in patch:
            runtime_patch["state"] = "active" if patch["enabled"] else "disabled"
        if "state" in patch:
            state = str(patch["state"]).lower()
            if state not in ("active", "disabled", "archived"):
                return {}, None, "state 仅支持 active/disabled/archived"
            runtime_patch["state"] = state
        if "catch_up" in patch:
            runtime_patch["catch_up"] = bool(patch["catch_up"])
        for key in ("timeout_sec", "run_timeout_sec"):
            if key in patch:
                try:
                    runtime_patch["run_timeout_sec"] = float(patch[key])
                except (TypeError, ValueError):
                    return {}, None, f"{key} 需为数字"
        schedule_patch = None
        if "schedule" in patch:
            schedule_patch, error = self._validate_schedule_patch(patch["schedule"])
            if error:
                return {}, None, error
        return runtime_patch, schedule_patch, ""

    def _validate_schedule_patch(self, sched: object) -> tuple[dict | None, str]:
        if not isinstance(sched, dict):
            return None, "schedule 需为对象"
        stype = str(sched.get("type", "")).lower()
        if stype not in ("daily", "weekly", "interval"):
            return None, "schedule.type 仅支持 daily/weekly/interval"
        if stype in ("daily", "weekly"):
            at = sched.get("at")
            ok_at = isinstance(at, str) or (isinstance(at, list) and len(at) > 0)
            if not ok_at:
                return None, f'{stype} 的 at 需为 "HH:MM" 或非空时刻数组'
        if stype == "weekly":
            days = sched.get("days")
            if not isinstance(days, list) or not days:
                return None, "weekly 的 days 需为非空数组（1=周一 … 7=周日）"
            if any(not isinstance(d, int) or not (1 <= d <= 7) for d in days):
                return None, "weekly 的 days 需为 1~7 的整数"
        if stype == "interval":
            try:
                every = float(sched.get("every_sec", 0))
            except (TypeError, ValueError):
                return None, "every_sec 需为数字"
            if every < 10:
                return None, "every_sec 需 >= 10 秒"
        return sched, ""

    def _commit_patch(self, name: str, spec: CronSpec, runtime_patch: dict,
                      schedule_patch: dict | None) -> tuple[bool, str, str]:
        """写回注册条并重解析；新配置无效则回滚。"""
        old_settings = dict(spec.raw.get("settings") or {})
        old_schedule = spec.raw.get("schedule")
        ok = True
        if runtime_patch:
            ok = self._set_settings(name, runtime_patch) and ok
        if schedule_patch is not None:
            ok = self._set_schedule(name, schedule_patch) and ok
        if not ok:
            return False, "io", "写注册条失败"

        new_spec = CronSpec(name, spec.reg_path)
        if not new_spec.valid:
            if runtime_patch:
                self._set_settings(name, old_settings)
            if schedule_patch is not None and old_schedule is not None:
                self._set_schedule(name, old_schedule)
            self.scan()
            return False, "invalid", f"{new_spec.error or '新配置无效'}（已回滚）"
        self.specs[name] = new_spec
        if schedule_patch is not None:
            p = self.persist.setdefault(name, {})
            p["next_run"] = self._init_next_run(new_spec, time.time())
        self._save_state()
        return True, "ok", "已更新"

    def toggle_enabled(self, name: str, value: bool) -> tuple[bool, str]:
        """开关任务调度（面板滑块）：权威值写回注册条 runtime.enabled。

        转调 update_config（同一写入 + 写后校验路径）。
        只影响下一次触发；正在跑的一轮让它自然跑完，不打断。
        """
        ok, _code, msg = self.update_config(name, {"enabled": value})
        return ok, msg

    def run_now(self, name: str, via: str = "manual") -> tuple[bool, str, str]:
        """手动立即触发（面板按钮 / CLI / API）。

        返回 (ok, code, message)；code 供 API 层映射 HTTP 状态：
        ok→202 / not_found→404 / invalid→400 / busy|disabled→409。
        """
        with self.lock:
            spec = self.specs.get(name)
            if spec is None:
                return False, "not_found", f"未知定时任务 {name}"
            if not spec.valid:
                return False, "invalid", f"注册条无效: {spec.error}"
            if name in self.running:
                # tick 与手动触发的并发窗口：_execute 进入即持锁 add running 兜底，
                # 此处预检只是快速拒绝，真正的防重叠判定在 _execute 的锁内
                return False, "busy", "上一轮仍在运行（防重叠，请稍后再试）"
            if not spec.enabled:
                return False, "disabled", "任务已被 enabled:false 停用"
            threading.Thread(target=self._execute, args=(name, spec, via),
                             daemon=True, name=f"cron-{name}").start()
            return True, "ok", "已触发"
