r"""my-service · core/scanner.py — 注册中心（reg/services 唯一源）

发现机制：
- 文件形态：`reg\services\<名>.json`
- 目录形态：`reg\services\<名>\reg.json`（转接层；path 缺省为本目录）

每个条目按 docs/design/config-schema.md 解析，分组为：
    start / stop / identity / settings / schedule

不保留旧字段兼容：
- `health` 并入 `identity`
- `runtime` 改名 `settings`
- `enabled` / `archived` 合并为 `settings.state`
- `identity.match` 由 process 是否含路径自动推断
"""
from __future__ import annotations

import json
from pathlib import Path

# 空闲回收阈值边界（秒）：clamp 1 分钟 ~ 24 小时，防误配导致抖动或永不回收。
IDLE_MIN_SEC = 60.0
IDLE_MAX_SEC = 86400.0
INTERVAL_MIN_SEC = 10.0
STATES = ("active", "disabled", "archived")


class Spec:
    """单个服务条目的解析结果。valid=False 时 error 携带原因，不参与编排。"""

    def __init__(self, name: str, reg_path: Path):
        self.folder = name                       # 服务名 = 注册文件名（去 .json）
        self.reg_path = reg_path                 # reg\services\<name>.json 或 <name>\reg.json
        self.raw: dict = {}
        self.valid: bool = True
        self.error: str | None = None
        self.derived: list[dict] = []    # 自动推导的值（check 展示）
        self.defaults: list[dict] = []   # 未写但生效的默认值（check 展示）
        self.path: Path = Path(".")

        # ---- 公共 ----
        self.display: str = name
        self.description: str = ""
        self.mode: str = ""                      # managed | on_demand | task（必写）
        self.url: str | None = None              # 前端页面地址（可选，与探测无关）

        # ---- start ----
        self.entry: str = ""
        self.args: list[str] = []
        self.cwd: str = "."
        self.env: dict[str, str] = {}
        self.start_detach: bool = False

        # ---- stop ----
        self.stop_script: str | None = None
        self.stop_grace: float = 10.0

        # ---- identity（身份 + 健康）----
        # 探测方法按优先级排列（http → port → process）：identity 可同时声明多个，
        # 健康判定只取最靠前的一个；其余成员仍参与身份判定（identify）。
        self.probes: tuple[str, ...] = ()
        self.health_port: int | None = None
        self.health_url: str | None = None
        self.health_timeout: float = 30.0
        self.health_service: str | None = None   # 对应 identity.service_name
        self.pidfile: str | None = None
        self.identity_process: str | None = None
        self.identity_match: str = "name"        # 自动推断：含路径 → path

        # ---- settings ----
        self.state: str = "active"               # active | disabled | archived
        self.autostart: bool = False
        self.order: float | None = None
        self.idle_timeout: float = 600.0
        self.run_timeout: float = 300.0
        self.catch_up: bool = False

        # ---- schedule（仅 task）----
        self.schedule_type: str = ""             # daily | weekly | interval
        self.schedule_times: list[tuple[int, int]] = []
        self.schedule_days: list[int] = []       # weekly：weekday 0=周一 … 6=周日
        self.every_sec: float = 0.0
        self.schedule_active: bool = True        # weekly days 为空 → False（不调度）

        try:
            data = json.loads(reg_path.read_text(encoding="utf-8-sig"))
            if not isinstance(data, dict):
                raise ValueError("注册条根节点必须是 JSON 对象")
            self.raw = data
        except Exception as exc:  # noqa: BLE001
            self.valid = False
            self.error = f"注册条解析失败: {exc}"
            return

        self._parse(data)

    # ---------------------------------------------------------------- 派生
    @property
    def enabled(self) -> bool:
        """兼容既有调用：state != disabled。"""
        return self.state != "disabled"

    @property
    def archived(self) -> bool:
        return self.state == "archived"

    @property
    def is_task(self) -> bool:
        return self.mode == "task"

    @property
    def probe(self) -> str:
        """生效的健康判据：已声明方法里优先级最高的一个；无声明为 "none"。"""
        return self.probes[0] if self.probes else "none"

    # ---------------------------------------------------------------- 解析
    def _parse(self, data: dict) -> None:
        steps = (
            self._reject_legacy,
            self._apply_path,
            self._apply_basic,
            self._apply_start,
            self._apply_stop,
            self._apply_identity,
            self._apply_settings,
            self._apply_schedule,
        )
        for step in steps:
            if step(data) is False:
                return
        self._validate()

    def _reject_legacy(self, data: dict) -> bool:
        legacy = [k for k in ("entry", "stop_script", "args", "cwd") if k in data]
        if legacy:
            self._invalid(f"旧版字段 {legacy} 已废弃，请改为 start/stop 分组")
            return False
        if "health" in data:
            self._invalid("health 已并入 identity（http/port/pidfile/process）")
            return False
        if "runtime" in data:
            self._invalid("runtime 已改名 settings（state/autostart/order/超时）")
            return False
        return True

    def _apply_path(self, data: dict) -> bool:
        raw = data.get("path")
        if isinstance(raw, str) and raw.strip():
            cand = Path(raw.strip())
            if not cand.is_absolute():
                cand = (self.reg_path.parent / cand).resolve()
            self.path = cand.resolve()
        elif self.reg_path.name == "reg.json":
            self.path = self.reg_path.parent.resolve()   # 目录形态：path 缺省为本目录
        else:
            self._invalid("缺少 path 字段（服务真实目录）")
            return False
        if not self.path.is_dir():
            self._invalid(f"path 不是存在的目录: {self.path}")
            return False
        return True

    def _apply_basic(self, data: dict) -> bool:
        raw_mode = data.get("mode")
        if not isinstance(raw_mode, str) or not raw_mode.strip():
            self._invalid("缺少 mode（managed / on_demand / task）")
            return False
        self.mode = raw_mode.strip().lower()
        if not data.get("display"):
            self.defaults.append({"field": "display", "value": self.folder})
        self.display = str(data.get("display") or self.folder)
        self.description = str(data.get("description") or "")
        u = data.get("url")
        self.url = u.strip() if isinstance(u, str) and u.strip() else None
        return True

    def _apply_start(self, data: dict) -> bool:
        start = data.get("start")
        if not isinstance(start, dict):
            self._invalid("缺少 start 分组（entry 必须显式声明）")
            return False
        self.entry = str(start.get("entry") or "").strip()
        args = start.get("args") or []
        self.args = [str(a) for a in args] if isinstance(args, list) else []
        self.cwd = str(start.get("cwd") or ".")
        if isinstance(start.get("env"), dict):
            self.env = {str(k): str(v) for k, v in start["env"].items()}
        self.start_detach = bool(start.get("detach", False))
        return True

    def _apply_stop(self, data: dict) -> bool:
        stop = data.get("stop")
        script = stop.get("script") if isinstance(stop, dict) else None
        if not isinstance(script, str) or not script.strip():
            self._invalid("缺少 stop.script（启停都必须显式声明）")
            return False
        self.stop_script = script.strip()
        self.stop_grace = _to_float(stop.get("grace_sec"), 10.0)
        return True

    def _apply_identity(self, data: dict) -> bool:
        ident = data.get("identity")
        if not isinstance(ident, dict):
            self._invalid("缺少 identity 分组（至少声明 http/port/pidfile/process 之一）")
            return False
        if not self._parse_identity_probe(ident):
            return False
        self._parse_identity_meta(ident)
        if not self._pick_probes():
            self._invalid("identity 至少需要 http/port/pidfile/process 之一")
            return False
        return True

    def _parse_identity_probe(self, ident: dict) -> bool:
        http = ident.get("http")
        if isinstance(http, str) and http.strip():
            self.health_url = http.strip()
        if "port" in ident:
            try:
                self.health_port = int(ident["port"])
            except (TypeError, ValueError):
                self._invalid("identity.port 需为整数")
                return False
        pidfile = ident.get("pidfile")
        if isinstance(pidfile, str) and pidfile.strip():
            self.pidfile = pidfile.strip()
        process = ident.get("process")
        if isinstance(process, str) and process.strip():
            self.identity_process = process.strip()
            # 自动推断匹配方式：含路径分隔符按完整路径，否则按进程名
            self.identity_match = (
                "path" if ("/" in self.identity_process
                           or "\\" in self.identity_process) else "name")
            if self.identity_match == "path":
                self.derived.append({
                    "field": "identity.process.match", "value": "path",
                    "reason": "process 含路径分隔符"})
        return True

    def _parse_identity_meta(self, ident: dict) -> None:
        svc = ident.get("service_name")
        if isinstance(svc, str) and svc.strip():
            self.health_service = svc.strip()
        if "timeout_sec" not in ident:
            self.defaults.append({"field": "identity.timeout_sec", "value": 30})
        self.health_timeout = _to_float(ident.get("timeout_sec"), 30.0)

    def _pick_probes(self) -> bool:
        """按优先级排列已声明的探测方法：http → port → process。

        首个即健康判据（失败不回退）；多声明时其余成员只做身份判定。
        """
        probes: list[str] = []
        if self.health_url:
            probes.append("http")
        if self.health_port is not None:
            probes.append("port")
        if self.pidfile or self.identity_process:
            probes.append("process")
        self.probes = tuple(probes)
        return bool(probes)

    def _apply_settings(self, data: dict) -> bool:
        settings = data.get("settings")
        if settings is None:
            settings = {}
        if not isinstance(settings, dict):
            self._invalid("settings 需为对象")
            return False
        state = str(settings.get("state") or "active").lower()
        if "state" not in settings:
            self.defaults.append({"field": "settings.state", "value": "active"})
        if "idle_timeout_sec" not in settings:
            self.defaults.append({"field": "settings.idle_timeout_sec", "value": 600})
        if "run_timeout_sec" not in settings:
            self.defaults.append({"field": "settings.run_timeout_sec", "value": 300})
        if state not in STATES:
            self._invalid(f"settings.state 仅支持 {'/'.join(STATES)}，得到 {state!r}")
            return False
        self.state = state
        self.autostart = bool(settings.get("autostart", False))
        if "order" in settings:
            try:
                self.order = float(settings["order"])
            except (TypeError, ValueError):
                self.order = None
        self.idle_timeout = _to_float(settings.get("idle_timeout_sec"), 600.0)
        self.run_timeout = _to_float(settings.get("run_timeout_sec"), 300.0)
        self.catch_up = bool(settings.get("catch_up", False))
        return True

    def _apply_schedule(self, data: dict) -> bool:
        if self.mode != "task":
            return True
        raw_sched = data.get("schedule")
        sched = raw_sched
        if sched is None:
            sched = {}                      # task 缺省：daily 09:00
            self.defaults.append({"field": "schedule", "value": "daily 09:00"})
        if not isinstance(sched, dict):
            self._invalid("schedule 需为对象")
            return False
        stype = str(sched.get("type") or "daily").lower()
        if stype not in ("daily", "weekly", "interval"):
            self._invalid(f"schedule.type 仅支持 daily/weekly/interval，得到 {stype!r}")
            return False
        self.schedule_type = stype
        if stype in ("daily", "weekly") and not self._parse_schedule_times(sched):
            return False
        if stype == "weekly":
            self._parse_schedule_days(sched)
            if not self.schedule_days:
                self.derived.append({
                    "field": "schedule.active", "value": False,
                    "reason": "未选择星期，任务不会执行"})
        if stype == "interval":
            return self._parse_schedule_interval(sched)
        return True

    def _parse_schedule_times(self, sched: dict) -> bool:
        at = sched.get("at")
        if isinstance(at, str):
            at = [at]
        if not isinstance(at, list) or not at:
            at = ["09:00"]
        for item in at:
            parsed = _parse_hhmm(str(item))
            if parsed:
                self.schedule_times.append(parsed)
        if not self.schedule_times:
            self._invalid("schedule.at 需为 HH:MM")
            return False
        return True

    def _parse_schedule_days(self, sched: dict) -> None:
        days = sched.get("days")
        if isinstance(days, list):
            for item in days:
                try:
                    d = int(item)
                except (TypeError, ValueError):
                    continue
                if 1 <= d <= 7:
                    self.schedule_days.append(d - 1)
        self.schedule_days = sorted(set(self.schedule_days))
        # 空 days = 不调度（valid，但不会执行）
        self.schedule_active = bool(self.schedule_days)

    def _parse_schedule_interval(self, sched: dict) -> bool:
        self.every_sec = _to_float(sched.get("every_sec"), 3600.0)
        if self.every_sec < INTERVAL_MIN_SEC:
            self._invalid(f"interval.every_sec 不能小于 {INTERVAL_MIN_SEC:.0f} 秒")
            return False
        return True

    # ---------------------------------------------------------------- 校验
    def _validate(self) -> None:
        problems: list[str] = []
        problems += self._check_mode_entry()
        problems += self._check_identity()
        problems += self._check_mode_rules()
        problems += self._check_idle_timeout()
        if problems:
            self.valid = False
            self.error = "；".join(problems)

    def _check_mode_entry(self) -> list[str]:
        out: list[str] = []
        if self.mode not in ("managed", "on_demand", "task"):
            out.append(f"mode 仅支持 managed/on_demand/task，得到 {self.mode!r}")
        if not self.entry:
            out.append("start.entry 不能为空")
        elif not self.entry_abs.is_file():
            out.append(f"入口文件不存在: {self.entry_abs}")
        return out

    def _check_identity(self) -> list[str]:
        if not self.probes:
            return ["identity 至少需要 http/port/pidfile/process 之一"]
        if "http" in self.probes and not self.health_url:
            return ["identity.http 不能为空"]
        if "port" in self.probes and self.health_port is None:
            return ["identity.port 不能为空"]
        if "process" in self.probes and not (self.pidfile or self.identity_process):
            return ["process 型身份需要 identity.pidfile 或 identity.process"]
        return []

    def _check_mode_rules(self) -> list[str]:
        if self.mode == "task":
            out: list[str] = []
            if self.start_detach:
                out.append("mode=task 不支持 start.detach")
            return out
        out = []
        if self.start_detach and self.mode != "managed":
            out.append("start.detach 仅允许 mode=managed")
        if self.mode == "on_demand" and self.start_detach:
            out.append("mode=on_demand 不支持 start.detach")
        return out

    def _check_idle_timeout(self) -> list[str]:
        if self.idle_timeout != 0 and not (IDLE_MIN_SEC <= self.idle_timeout <= IDLE_MAX_SEC):
            return [
                f"settings.idle_timeout_sec 需为 0 或 "
                f"{int(IDLE_MIN_SEC)}~{int(IDLE_MAX_SEC)} 秒"]
        return []

    def _invalid(self, msg: str) -> None:
        self.valid = False
        self.error = msg

    # ---------------------------------------------------------------- 路径
    def _resolve(self, rel: str) -> Path:
        """相对 path 解析（服务家）。"""
        return (self.path / rel).resolve()

    @property
    def workdir(self) -> Path:
        return (self.path / self.cwd).resolve()

    @property
    def entry_abs(self) -> Path:
        return self._resolve(self.entry)

    @property
    def stop_script_abs(self) -> Path | None:
        if not self.stop_script:
            return None
        return self._resolve(self.stop_script)


def _to_float(value: object, default: float) -> float:
    """宽松转 float：None / 非法值一律回落到默认值。"""
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _parse_hhmm(text: str) -> tuple[int, int] | None:
    try:
        hh, mm = text.strip().split(":")
        h, m = int(hh), int(mm)
        if 0 <= h <= 23 and 0 <= m <= 59:
            return h, m
    except (ValueError, AttributeError):
        pass
    return None


class Scanner:
    r"""扫描 reg\services\*.json 与 reg\services\<名>\reg.json —— 唯一发现源。"""

    def __init__(self, reg_dir: Path):
        self.reg_dir = reg_dir.resolve()

    def scan(self) -> dict[str, Spec]:
        specs: dict[str, Spec] = {}
        if not self.reg_dir.is_dir():
            return specs
        for f in sorted(self.reg_dir.glob("*.json")):
            spec = Spec(f.stem, f)
            if spec.archived:
                continue
            specs[spec.folder] = spec
        for d in sorted(p for p in self.reg_dir.iterdir() if p.is_dir()):
            reg = d / "reg.json"
            if not reg.is_file():
                continue
            spec = Spec(d.name, reg)
            if spec.archived:
                continue
            specs[spec.folder] = spec      # 目录形态优先于同名文件
        return specs
