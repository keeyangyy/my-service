# -*- coding: utf-8 -*-
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
- 状态持久化 logs\\cron_state.json（原子写）：last_run/last_ok/last_rc/
  last_dur_ms/next_run，宿主重启后按此恢复计划

日志边界（与服务同一条铁律）：脚本输出一律 DEVNULL，任务日志由任务自己写；
框架只把"何时触发了、成没成、耗时、rc"记进登记簿（cron_run/cron_skip）。
"""
from __future__ import annotations

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

    # 运行态字段：生效值取自 logs\cron_runtime.json，注册条里的同名键只作回落——
    # enabled/timeout_sec/catch_up 回落未迁移旧数据；schedule 回落注册条里留的
    # "默认计划"（账本被清时不至于让任务哑掉）。
    RUNTIME_KEYS = ("enabled", "timeout_sec", "catch_up", "schedule")

    def __init__(self, name: str, reg_path: Path, runtime: dict | None = None):
        self.folder = name                        # 任务名 = 注册文件名（去 .json）
        self.reg_path = reg_path                  # reg\cron\<name>.json（只读声明）
        self.raw: dict = {}                       # 注册条原文（声明 + 默认计划）
        self.runtime: dict = {}                   # 本条运行态账面值（生效值来源）
        self.valid: bool = True
        self.error: str | None = None
        self.path: Path = Path('.')
        self.enabled: bool = True
        self.display: str = name
        self.description: str = ""
        self.entry: str = ""
        self.args: list[str] = []
        self.cwd: str = "."
        self.env: dict[str, str] = {}
        self.schedule_type: str = ""              # daily | interval
        self.schedule_times: list[tuple[int, int]] = []   # daily 的时刻列表（解析时排序去重）
        self.every_sec: float = 0.0               # interval 的周期
        self.timeout_sec: float = 300.0
        self.catch_up: bool = False

        try:
            text = reg_path.read_text(encoding="utf-8-sig")
            data = json.loads(text)
            if not isinstance(data, dict):
                raise ValueError("注册条根节点必须是 JSON 对象")
            self.raw = data
        except Exception as exc:  # noqa: BLE001 - 坏注册条一律变 invalid
            self.valid = False
            self.error = f"注册条解析失败: {exc}"
            return

        # ---- 声明 + 运行态合并：账本值覆盖注册条同名键（后者只作回落）----
        self.runtime = {k: runtime[k] for k in self.RUNTIME_KEYS
                        if isinstance(runtime, dict) and k in runtime}
        view = {**data, **self.runtime}

        problems: list[str] = []

        # ---- path（与服务注册条同语义：任务脚本的真实家）----
        raw_path = view.get("path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            problems.append("缺少 path 字段（脚本所在目录）")
        else:
            cand = Path(raw_path.strip())
            if not cand.is_absolute():
                cand = (reg_path.parent / cand).resolve()
            self.path = cand.resolve()
            if not self.path.is_dir():
                problems.append(f"path 不是存在的目录: {self.path}")

        self.enabled = bool(view.get("enabled", True))
        self.display = str(view.get("display", name))
        self.description = str(view.get("description", ""))
        self.entry = str(view.get("entry", "")).strip()
        args = view.get("args", [])
        self.args = [str(a) for a in args] if isinstance(args, list) else []
        self.cwd = str(view.get("cwd", "."))
        self.catch_up = bool(view.get("catch_up", False))
        try:
            self.timeout_sec = float(view.get("timeout_sec", 300.0))
        except (TypeError, ValueError):
            problems.append("timeout_sec 不是数字")
        self.timeout_sec = max(5.0, min(self.timeout_sec, 86400.0))
        if isinstance(view.get("env"), dict):
            self.env = {str(k): str(v) for k, v in view["env"].items()}

        # ---- schedule（必需，物种核心字段）----
        sched = view.get("schedule")
        if not isinstance(sched, dict):
            problems.append('缺少 schedule 字段（{"type":"daily","at":"HH:MM"} '
                            '或 {"type":"interval","every_sec":N}）')
        else:
            stype = str(sched.get("type", "")).lower()
            if stype == "daily":
                self.schedule_type = "daily"
                raw_at = sched.get("at")
                raw_at = [raw_at] if isinstance(raw_at, str) else raw_at
                if not isinstance(raw_at, list) or not raw_at:
                    problems.append('daily 的 at 需为 "HH:MM" 或 ["HH:MM", ...]（至少一个时刻）')
                else:
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
                        problems.append(f"daily 的 at 含非法时刻（需 HH:MM）: {bad}")
                    else:
                        self.schedule_times = sorted(set(times))
                        if not self.schedule_times:
                            problems.append("daily 的 at 不能为空")
            elif stype == "interval":
                self.schedule_type = "interval"
                try:
                    self.every_sec = float(sched.get("every_sec", 0))
                except (TypeError, ValueError):
                    self.every_sec = 0
                if self.every_sec < 5:
                    problems.append("interval 的 every_sec 需 >= 5 秒")
                else:
                    self.every_sec = min(self.every_sec, 604800.0)  # ≤ 7 天
            else:
                problems.append(f"schedule.type 仅支持 daily/interval，得到 {stype!r}")

        # ---- 入口存在性 ----
        if not self.entry:
            problems.append("缺少 entry 字段")
        elif self.entry_abs and not self.entry_abs.is_file():
            problems.append(f"入口文件不存在: {self.entry_abs}")

        if problems:
            self.valid = False
            self.error = "；".join(problems)

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


class Scheduler:
    """定时任务编排器：扫描 reg\\cron\\ + 每秒调度 tick + 到点开线程执行。"""

    def __init__(self, cron_dir: Path, state_path: Path, order_path: Path | None = None,
                 runtime_path: Path | None = None):
        """runtime_path: 运行态账本（logs\\cron_runtime.json 的家）；缺省取
        state_path 同目录的 cron_runtime.json。
        """
        self.cron_dir = Path(cron_dir)
        self.state_path = Path(state_path)
        self.order_path = Path(order_path) if order_path else None
        self.runtime_path = (Path(runtime_path) if runtime_path is not None
                             else self.state_path.parent / "cron_runtime.json")
        self.specs: dict[str, CronSpec] = {}
        self.persist: dict[str, dict] = {}   # name -> last_run/last_ok/.../next_run
        self._order: list[str] = []          # 面板拖拽顺序（持久化在 order_path）
        self._runtime: dict[str, dict] = {}  # 运行态账面值（持久化在 runtime_path）
        self.running: set[str] = set()
        self.lock = threading.RLock()
        self._load_state()
        self._load_order()
        self._load_runtime()

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
        """按拖拽顺序返回任务名；未入册的新任务按字母序追加末尾（不打乱已有顺序）。"""
        known = set(self.specs)
        picked = [n for n in self._order if n in known]
        picked += sorted(known - set(picked))
        return picked

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

    # ------------------------------------------------------------ 运行态账本
    def _load_runtime(self) -> None:
        """读 logs\\cron_runtime.json；缺失/损坏都从零开始（回落注册条或字段缺省）。"""
        try:
            data = json.loads(self.runtime_path.read_text(encoding="utf-8-sig"))
            if isinstance(data, dict):
                self._runtime = {str(k): v for k, v in data.items()
                                 if isinstance(v, dict)}
        except Exception:  # noqa: BLE001
            self._runtime = {}

    def _save_runtime(self) -> bool:
        """原子写运行态账本（临时文件 + os.replace）。失败静默——调度不该被它拖垮。"""
        try:
            import tempfile

            self.runtime_path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=str(self.runtime_path.parent),
                                       suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(self._runtime, ensure_ascii=False, indent=1) + "\n")
            os.replace(tmp, self.runtime_path)
            return True
        except OSError:
            return False

    def runtime_snapshot(self) -> dict[str, dict]:
        """运行态账面值快照（每轮 scan 取用）。"""
        with self.lock:
            return {k: dict(v) for k, v in self._runtime.items()}

    def _set_runtime(self, name: str, patch: dict) -> bool:
        """只写运行态账本（白名单过滤，绝不碰注册条）。返回是否落盘成功。"""
        with self.lock:
            cur = dict(self._runtime.get(name) or {})
            cur.update({k: v for k, v in patch.items() if k in CronSpec.RUNTIME_KEYS})
            self._runtime[name] = cur
            return self._save_runtime()

    # ------------------------------------------------------------ 发现
    def scan(self) -> None:
        specs: dict[str, CronSpec] = {}
        runtime = self.runtime_snapshot()
        if self.cron_dir.is_dir():
            for f in sorted(self.cron_dir.glob("*.json")):
                spec = CronSpec(f.stem, f, runtime=runtime.get(f.stem))
                specs[spec.folder] = spec
        with self.lock:
            gone = [n for n in self.specs if n not in specs]
            self.specs = specs
            stale = [n for n in self._order if n not in specs]
            for name in gone:
                self.persist.pop(name, None)   # 注册条已删：账本条目一并清
                self._runtime.pop(name, None)  # 运行态账本条目一并清
                self.running.discard(name)
                self._save_state()
                self._save_runtime()
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
                    "enabled": spec.enabled,
                    "valid": spec.valid,
                    "error": spec.error,
                    "schedule": spec.schedule_desc,
                    # 编辑控件用的原始参数（schedule_desc 仅供展示）
                    "schedule_type": spec.schedule_type,
                    "at": ([f"{h:02d}:{m:02d}" for h, m in spec.schedule_times]
                           if spec.schedule_times else None),
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
            try:
                self.tick()
            except Exception:  # noqa: BLE001 - 调度线程永不退出
                pass
            time.sleep(TICK_SEC)

    def _init_next_run(self, spec: CronSpec, now: float) -> float:
        """新任务的首次计划：daily 排到下一个时刻；interval 起算一个周期。"""
        if spec.schedule_type == "daily" and spec.schedule_times:
            return _next_daily(spec.schedule_times, now)
        return now + spec.every_sec

    def _defer_after_miss(self, spec: CronSpec, now: float) -> float:
        """错过且不补跑时的顺延目标：绝不回头补，只往前排。"""
        if spec.schedule_type == "daily" and spec.schedule_times:
            return _next_daily(spec.schedule_times, now)
        return now + spec.every_sec

    def _defer_after_run(self, spec: CronSpec, last_run: float, now: float) -> float:
        """执行完毕后的下一次计划：interval 按上次起算（已过期则从现在重新起算）。"""
        if spec.schedule_type == "daily" and spec.schedule_times:
            return _next_daily(spec.schedule_times, now)
        cand = last_run + spec.every_sec
        if cand <= now + 1:
            cand = now + spec.every_sec
        return cand

    def tick(self) -> None:
        self.scan()
        now = time.time()
        with self.lock:
            for name, spec in list(self.specs.items()):
                if not spec.valid or not spec.enabled or name in self.running:
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
            p = self.persist.setdefault(name, {})
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
        """面板行内编辑：改 schedule / timeout_sec / catch_up / enabled（全是运行态）。

        只写 logs\\cron_runtime.json，注册条一个字节不动。安全网保留：语法校验 →
        记账本 → 用新运行态重解析 CronSpec 校验，判无效则**回滚账本**并报错——
        绝不留一个坏配置（否则任务直接变 invalid 不可用）。
        schedule 变更时重算 next_run（否则面板显示新时间、实际仍按旧时间触发）。

        返回 (ok, code, message)；code 同 run_now 的映射约定。
        """
        with self.lock:
            spec = self.specs.get(name)
            if spec is None:
                return False, "not_found", f"未知定时任务 {name}"

            # ---- 应用补丁（粗校验，细校验交给写后重解析）----
            values: dict = {}
            if "enabled" in patch:
                values["enabled"] = bool(patch["enabled"])
            if "catch_up" in patch:
                values["catch_up"] = bool(patch["catch_up"])
            if "timeout_sec" in patch:
                try:
                    values["timeout_sec"] = float(patch["timeout_sec"])
                except (TypeError, ValueError):
                    return False, "invalid", "timeout_sec 需为数字"
            if "schedule" in patch:
                sched = patch["schedule"]
                if not isinstance(sched, dict):
                    return False, "invalid", "schedule 需为对象"
                stype = str(sched.get("type", "")).lower()
                if stype not in ("daily", "interval"):
                    return False, "invalid", "schedule.type 仅支持 daily/interval"
                if stype == "daily":
                    at = sched.get("at")
                    ok_at = (isinstance(at, str) or
                             (isinstance(at, list) and len(at) > 0))
                    if not ok_at:
                        return False, "invalid", 'daily 的 at 需为 "HH:MM" 或非空时刻数组'
                values["schedule"] = sched
            if not values:
                return False, "invalid", "没有需要修改的字段"

            old_runtime = dict(self._runtime.get(name) or {})
            if not self._set_runtime(name, values):
                return False, "io", "写运行态账本失败"

            # ---- 写后校验：新运行态必须让注册条仍然合法，否则回滚账本 ----
            new_spec = CronSpec(name, spec.reg_path, runtime=self._runtime.get(name))
            if not new_spec.valid:
                self._runtime[name] = old_runtime
                self._save_runtime()
                self.scan()
                return False, "invalid", f"新配置无效，已回滚：{new_spec.error}"

            self.specs[name] = new_spec
            if "schedule" in values:
                p = self.persist.setdefault(name, {})
                p["next_run"] = self._init_next_run(new_spec, time.time())
            self._save_state()
            return True, "ok", "已更新"

    def toggle_enabled(self, name: str, value: bool) -> tuple[bool, str]:
        """开关任务调度（面板滑块）：权威值只写运行态账本（logs\\cron_runtime.json）。

        转调 update_config（同一账本写入 + 写后校验路径）。
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
