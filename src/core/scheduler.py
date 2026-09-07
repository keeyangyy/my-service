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

    def __init__(self, name: str, reg_path: Path):
        self.folder = name                        # 任务名 = 注册文件名（去 .json）
        self.reg_path = reg_path
        self.raw: dict = {}
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
        self.schedule_at: tuple[int, int] | None = None   # daily 的 (h, m)
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

        problems: list[str] = []

        # ---- path（与服务注册条同语义：任务脚本的真实家）----
        raw_path = data.get("path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            problems.append("缺少 path 字段（脚本所在目录）")
        else:
            cand = Path(raw_path.strip())
            if not cand.is_absolute():
                cand = (reg_path.parent / cand).resolve()
            self.path = cand.resolve()
            if not self.path.is_dir():
                problems.append(f"path 不是存在的目录: {self.path}")

        self.enabled = bool(data.get("enabled", True))
        self.display = str(data.get("display", name))
        self.description = str(data.get("description", ""))
        self.entry = str(data.get("entry", "")).strip()
        args = data.get("args", [])
        self.args = [str(a) for a in args] if isinstance(args, list) else []
        self.cwd = str(data.get("cwd", "."))
        self.catch_up = bool(data.get("catch_up", False))
        try:
            self.timeout_sec = float(data.get("timeout_sec", 300.0))
        except (TypeError, ValueError):
            problems.append("timeout_sec 不是数字")
        self.timeout_sec = max(5.0, min(self.timeout_sec, 86400.0))
        if isinstance(data.get("env"), dict):
            self.env = {str(k): str(v) for k, v in data["env"].items()}

        # ---- schedule（必需，物种核心字段）----
        sched = data.get("schedule")
        if not isinstance(sched, dict):
            problems.append('缺少 schedule 字段（{"type":"daily","at":"HH:MM"} '
                            '或 {"type":"interval","every_sec":N}）')
        else:
            stype = str(sched.get("type", "")).lower()
            if stype == "daily":
                self.schedule_type = "daily"
                at = str(sched.get("at", "")).strip()
                try:
                    hh, mm = at.split(":")
                    self.schedule_at = (int(hh), int(mm))
                    if not (0 <= self.schedule_at[0] <= 23
                            and 0 <= self.schedule_at[1] <= 59):
                        raise ValueError
                except (ValueError, TypeError):
                    problems.append(f"daily 的 at 非法（需 HH:MM）: {at!r}")
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
        if self.schedule_type == "daily" and self.schedule_at:
            return f"每天 {self.schedule_at[0]:02d}:{self.schedule_at[1]:02d}"
        if self.schedule_type == "interval" and self.every_sec >= 5:
            if self.every_sec % 3600 == 0:
                return f"每 {int(self.every_sec // 3600)} 小时"
            if self.every_sec % 60 == 0:
                return f"每 {int(self.every_sec // 60)} 分钟"
            return f"每 {int(self.every_sec)} 秒"
        return "未知"


def _next_daily(at: tuple[int, int], after: float) -> float:
    """after 之后的下一个每日 HH:MM 时刻（本地时间）。"""
    dt = datetime.fromtimestamp(after)
    cand = dt.replace(hour=at[0], minute=at[1], second=0, microsecond=0)
    if cand.timestamp() <= after:
        cand += timedelta(days=1)
    return cand.timestamp()


class Scheduler:
    """定时任务编排器：扫描 reg\\cron\\ + 每秒调度 tick + 到点开线程执行。"""

    def __init__(self, cron_dir: Path, state_path: Path, order_path: Path | None = None):
        self.cron_dir = Path(cron_dir)
        self.state_path = Path(state_path)
        self.order_path = Path(order_path) if order_path else None
        self.specs: dict[str, CronSpec] = {}
        self.persist: dict[str, dict] = {}   # name -> last_run/last_ok/.../next_run
        self._order: list[str] = []          # 面板拖拽顺序（持久化在 order_path）
        self.running: set[str] = set()
        self.lock = threading.RLock()
        self._load_state()
        self._load_order()

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
                    "enabled": spec.enabled,
                    "valid": spec.valid,
                    "error": spec.error,
                    "schedule": spec.schedule_desc,
                    # 编辑控件用的原始参数（schedule_desc 仅供展示）
                    "schedule_type": spec.schedule_type,
                    "at": (f"{spec.schedule_at[0]:02d}:{spec.schedule_at[1]:02d}"
                           if spec.schedule_at else None),
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
        if spec.schedule_type == "daily" and spec.schedule_at:
            return _next_daily(spec.schedule_at, now)
        return now + spec.every_sec

    def _defer_after_miss(self, spec: CronSpec, now: float) -> float:
        """错过且不补跑时的顺延目标：绝不回头补，只往前排。"""
        if spec.schedule_type == "daily" and spec.schedule_at:
            return _next_daily(spec.schedule_at, now)
        return now + spec.every_sec

    def _defer_after_run(self, spec: CronSpec, last_run: float, now: float) -> float:
        """执行完毕后的下一次计划：interval 按上次起算（已过期则从现在重新起算）。"""
        if spec.schedule_type == "daily" and spec.schedule_at:
            return _next_daily(spec.schedule_at, now)
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
        """面板行内编辑：改注册条的 schedule / timeout_sec / catch_up / enabled。

        安全网：写回后立即用 CronSpec 重解析校验，判无效则回滚原内容并报错
        ——绝不留一个坏注册条（否则任务直接变 invalid 不可用）。
        schedule 变更时重算 next_run（否则面板显示新时间、实际仍按旧时间触发）。

        返回 (ok, code, message)；code 同 run_now 的映射约定。
        """
        with self.lock:
            spec = self.specs.get(name)
            if spec is None:
                return False, "not_found", f"未知定时任务 {name}"
            try:
                old_text = spec.reg_path.read_text(encoding="utf-8-sig")
                raw = json.loads(old_text)
                if not isinstance(raw, dict):
                    raise ValueError("注册条根节点不是对象")
            except Exception as exc:  # noqa: BLE001
                return False, "io", f"读注册条失败: {exc}"

            # ---- 应用补丁（粗校验，细校验交给写后重解析）----
            if "enabled" in patch:
                raw["enabled"] = bool(patch["enabled"])
            if "catch_up" in patch:
                raw["catch_up"] = bool(patch["catch_up"])
            if "timeout_sec" in patch:
                try:
                    raw["timeout_sec"] = float(patch["timeout_sec"])
                except (TypeError, ValueError):
                    return False, "invalid", "timeout_sec 需为数字"
            if "schedule" in patch:
                sched = patch["schedule"]
                if not isinstance(sched, dict):
                    return False, "invalid", "schedule 需为对象"
                stype = str(sched.get("type", "")).lower()
                if stype not in ("daily", "interval"):
                    return False, "invalid", "schedule.type 仅支持 daily/interval"
                raw["schedule"] = sched

            def _write(payload: dict) -> bool:
                try:
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
            new_spec = CronSpec(name, spec.reg_path)
            if not new_spec.valid:
                try:
                    _write(json.loads(old_text))
                except Exception:  # noqa: BLE001
                    pass
                self.scan()
                return False, "invalid", f"新配置无效，已回滚：{new_spec.error}"

            self.specs[name] = new_spec
            if "schedule" in patch:
                p = self.persist.setdefault(name, {})
                p["next_run"] = self._init_next_run(new_spec, time.time())
            self._save_state()
            return True, "ok", "已更新"

    def toggle_enabled(self, name: str, value: bool) -> tuple[bool, str]:
        """开关任务调度（面板滑块）：权威值回写注册条 enabled 字段。

        转调 update_config（同一原子写回 + 写后校验路径）。
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
