r"""my-service · core/scanner.py — 注册中心（reg/services 唯一源）

发现机制：只扫 `reg\services\*.json`。每个条目声明服务的真实家（path），
start/stop/health/identity 等相对 path 解析。

条目的六组（见 docs/design/registry.md）：
    start / stop / health / identity / runtime / schedule

不保留旧字段兼容层：旧结构（entry、stop_script、顶层 pidfile、detached、none）
一律判 invalid，迁移依靠一次性改写注册条。
"""
from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlsplit

# 空闲回收阈值边界（秒）：clamp 1 分钟 ~ 24 小时，防误配导致抖动或永不回收。
IDLE_MIN_SEC = 60.0
IDLE_MAX_SEC = 86400.0
INTERVAL_MIN_SEC = 10.0


class Spec:
    """单个服务条目的解析结果。valid=False 时 error 携带原因，不参与编排。"""

    def __init__(self, name: str, reg_path: Path, adapter_dir: Path | None = None):
        self.folder = name                       # 服务名 = 注册文件名（去 .json）
        self.reg_path = reg_path                 # reg\services\<name>.json
        self.raw: dict = {}
        self.valid: bool = True
        self.error: str | None = None
        self.path: Path = Path(".")
        self.adapter_dir = adapter_dir

        # ---- 公共 ----
        self.display: str = name
        self.description: str = ""
        self.mode: str = "managed"               # managed | on_demand | task
        self.url: str | None = None

        # ---- start ----
        self.entry: str = ""
        self.args: list[str] = []
        self.cwd: str = "."
        self.env: dict[str, str] = {}
        self.start_detach: bool = False          # true = 点火脚本，退场算正常

        # ---- stop ----
        self.stop_script: str | None = None
        self.stop_grace: float = 10.0

        # ---- health ----
        self.health_type: str = "http"           # http | port | process
        self.health_port: int | None = None
        self.health_url: str | None = None
        self.health_timeout: float = 30.0
        self.health_service: str | None = None

        # ---- identity ----
        self.pidfile: str | None = None
        self.identity_process: str | None = None
        self.identity_match: str = "name"        # name | path

        # ---- runtime ----
        self.enabled: bool = True
        self.autostart: bool = False
        self.archived: bool = False
        self.order: float | None = None
        self.idle_timeout: float = 600.0
        self.run_timeout: float = 300.0
        self.catch_up: bool = False

        # ---- schedule（仅 task）----
        self.schedule_type: str = ""
        self.schedule_times: list[tuple[int, int]] = []
        self.every_sec: float = 0.0

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

    # ---------------------------------------------------------------- 解析
    def _parse(self, data: dict) -> None:
        # 旧结构直接拒绝（不保留兼容层）
        legacy = [k for k in ("entry", "stop_script", "args", "cwd") if k in data]
        if legacy:
            self.valid = False
            self.error = (f"旧版字段 {legacy} 已废弃，请改为 start/stop 分组")
            return

        raw_path = data.get("path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            return self._invalid("缺少 path 字段（服务真实目录）")
        cand = Path(raw_path.strip())
        if not cand.is_absolute():
            cand = (self.reg_path.parent / cand).resolve()
        self.path = cand.resolve()
        if not self.path.is_dir():
            return self._invalid(f"path 不是存在的目录: {self.path}")

        self.display = str(data.get("display") or self.folder)
        self.description = str(data.get("description") or "")
        self.mode = str(data.get("mode") or "managed").lower()

        # start
        start = data.get("start")
        if not isinstance(start, dict):
            return self._invalid("缺少 start 分组")
        self.entry = str(start.get("entry") or "").strip()
        args = start.get("args") or []
        self.args = [str(a) for a in args] if isinstance(args, list) else []
        self.cwd = str(start.get("cwd") or ".")
        if isinstance(start.get("env"), dict):
            self.env = {str(k): str(v) for k, v in start["env"].items()}
        self.start_detach = bool(start.get("detach", False))

        # stop
        stop = data.get("stop")
        if isinstance(stop, dict):
            self.stop_script = str(stop.get("script") or "") or None
            try:
                self.stop_grace = float(stop.get("grace_sec", 10.0))
            except (TypeError, ValueError):
                self.stop_grace = 10.0

        # health
        health = data.get("health")
        if not isinstance(health, dict):
            return self._invalid("缺少 health 分组")
        self.health_type = str(health.get("type") or "").lower()
        try:
            self.health_port = int(health["port"]) if "port" in health else None
        except (TypeError, ValueError):
            self.health_port = None
        self.health_url = health.get("url")
        try:
            self.health_timeout = float(health.get("timeout_sec", 30.0))
        except (TypeError, ValueError):
            self.health_timeout = 30.0
        svc = health.get("service")
        self.health_service = str(svc).strip() if isinstance(svc, str) and svc.strip() else None

        # identity
        identity = data.get("identity")
        if isinstance(identity, dict):
            self.pidfile = str(identity.get("pidfile") or "") or None
            self.identity_process = str(identity.get("process") or "") or None
            self.identity_match = str(identity.get("match") or "name").lower()

        # runtime
        runtime = data.get("runtime")
        if isinstance(runtime, dict):
            self.enabled = bool(runtime.get("enabled", True))
            self.autostart = bool(runtime.get("autostart", False))
            self.archived = bool(runtime.get("archived", False))
            if "order" in runtime:
                try:
                    self.order = float(runtime["order"])
                except (TypeError, ValueError):
                    self.order = None
            try:
                self.idle_timeout = float(runtime.get("idle_timeout_sec", 600.0))
            except (TypeError, ValueError):
                self.idle_timeout = 600.0
            try:
                self.run_timeout = float(runtime.get("run_timeout_sec", 300.0))
            except (TypeError, ValueError):
                self.run_timeout = 300.0
            self.catch_up = bool(runtime.get("catch_up", False))

        # schedule（仅 task）
        schedule = data.get("schedule")
        if isinstance(schedule, dict):
            self.schedule_type = str(schedule.get("type") or "").lower()
            at = schedule.get("at")
            if isinstance(at, str):
                at = [at]
            if isinstance(at, list):
                for t in at:
                    parsed = _parse_hhmm(str(t))
                    if parsed:
                        self.schedule_times.append(parsed)
            try:
                self.every_sec = float(schedule.get("every_sec", 0.0))
            except (TypeError, ValueError):
                self.every_sec = 0.0

        # url 缺省从 health.url 推导
        u = data.get("url")
        if isinstance(u, str) and u.strip():
            self.url = u.strip()
        elif self.health_url:
            p = urlsplit(self.health_url)
            if p.scheme and p.netloc:
                self.url = f"{p.scheme}://{p.netloc}/"

        self._validate()

    # ---------------------------------------------------------------- 校验
    def _validate(self) -> None:
        problems: list[str] = []
        if self.mode not in ("managed", "on_demand", "task"):
            problems.append(f"mode 仅支持 managed/on_demand/task，得到 {self.mode!r}")
        if not self.entry:
            problems.append("start.entry 不能为空")
        elif not self.entry_abs.is_file():
            problems.append(f"入口文件不存在: {self.entry_abs}")

        if self.health_type not in ("http", "port", "process"):
            problems.append(f"health.type 仅支持 http/port/process，得到 {self.health_type!r}")
        elif self.health_type == "http" and not self.health_url:
            problems.append("health.type=http 需要 health.url")
        elif self.health_type == "port" and self.health_port is None:
            problems.append("health.type=port 需要 health.port")
        elif self.health_type == "process" and not (self.pidfile or self.identity_process):
            problems.append("health.type=process 需要 identity.pidfile 或 identity.process")

        if self.mode == "task":
            if not self.schedule_type:
                problems.append("mode=task 需要 schedule")
            elif self.schedule_type not in ("daily", "interval"):
                problems.append(f"schedule.type 仅支持 daily/interval，得到 {self.schedule_type!r}")
            elif self.schedule_type == "daily" and not self.schedule_times:
                problems.append("daily 需要至少一个 at 时刻（HH:MM）")
            elif self.schedule_type == "interval" and self.every_sec < INTERVAL_MIN_SEC:
                problems.append(f"interval.every_sec 不能小于 {INTERVAL_MIN_SEC:.0f} 秒")
            if self.start_detach:
                problems.append("mode=task 不支持 start.detach")
        else:
            if self.start_detach and self.mode != "managed":
                problems.append("start.detach 仅允许 mode=managed")
            if self.mode == "on_demand":
                if not self.stop_script:
                    problems.append("mode=on_demand 需要 stop.script（否则无法回收）")
                if self.start_detach:
                    problems.append("mode=on_demand 不支持 start.detach")

        if self.idle_timeout != 0 and not (IDLE_MIN_SEC <= self.idle_timeout <= IDLE_MAX_SEC):
            problems.append(
                f"runtime.idle_timeout_sec 需为 0 或 {int(IDLE_MIN_SEC)}~{int(IDLE_MAX_SEC)} 秒")

        if problems:
            self.valid = False
            self.error = "；".join(problems)

    def _invalid(self, msg: str) -> None:
        self.valid = False
        self.error = msg

    # ---------------------------------------------------------------- 路径
    def _resolve(self, rel: str) -> Path:
        r"""相对 path 解析；adapters\<svc>\ 优先，官方/原生脚本回退。"""
        if self.adapter_dir:
            cand = (self.adapter_dir / self.folder / rel).resolve()
            if cand.is_file():
                return cand
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

    @property
    def is_task(self) -> bool:
        return self.mode == "task"


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
    r"""扫描 reg\services\*.json —— 注册中心是唯一发现源。"""

    def __init__(self, reg_dir: Path):
        self.reg_dir = reg_dir.resolve()
        self.adapter_dir = reg_dir.parent.parent / "adapters"

    def scan(self) -> dict[str, Spec]:
        specs: dict[str, Spec] = {}
        if not self.reg_dir.is_dir():
            return specs
        for f in sorted(self.reg_dir.glob("*.json")):
            spec = Spec(f.stem, f, self.adapter_dir)
            if spec.archived:
                continue
            specs[spec.folder] = spec
        return specs
