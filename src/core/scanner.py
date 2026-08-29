# -*- coding: utf-8 -*-
"""my-service · core/scanner.py — 注册中心（reg 唯一源）

发现机制：只扫 my-service\\reg\\*.json —— 框架的"花名册"。
每个注册条声明服务的真实家（path）；entry/cwd/stop_script 等相对 path 解析。
服务本体（源码/数据/官方脚本）严禁进入 my-service；reg 只是账册。

铁律重申：
- 注册 = 在 reg\\ 放一个 <服务名>.json
- 注销 = 删除该文件或 "enabled": false
- 不改动第三方软件目录一个字节（some-service 便携目录零侵入）
"""
from __future__ import annotations

import json
from pathlib import Path


class Spec:
    """单个注册条目的解析结果。

    valid=False 时 error 携带原因，前端以 invalid 展示，不参与编排。
    """

    def __init__(self, name: str, reg_path: Path, adapter_dir: Path | None = None):
        self.folder = name                       # 服务名 = 注册文件名（去 .json）
        self.reg_path = reg_path                 # reg\\<name>.json（开关回写此处）
        self.raw: dict = {}
        self.valid: bool = True
        self.error: str | None = None
        self.path: Path = Path('.')              # 服务真实家（注册条 path 字段）
        self.adapter_dir = adapter_dir           # my-service\adapters（可选转接层根）

        # ---- 声明字段 ----
        self.enabled: bool = True
        self.display: str = name
        self.description: str = ""
        self.entry: str = ""
        self.args: list[str] = []
        self.cwd: str = "."
        self.mode: str = "managed"               # managed | detached（点火器型）
        self.health_type: str = "none"           # none | port | http | process
        self.health_port: int | None = None
        self.health_url: str | None = None
        self.health_timeout: float = 20.0
        self.env: dict[str, str] = {}
        self.autostart: bool = False
        self.url: str | None = None              # 服务自带网页，dashboard 一键跳转
        self.stop_script: str | None = None
        self.pidfile: str | None = None

        try:
            text = reg_path.read_text(encoding="utf-8-sig")
            data = json.loads(text)
            if not isinstance(data, dict):
                raise ValueError("注册条根节点必须是 JSON 对象")
            self.raw = data
        except Exception as exc:  # noqa: BLE001 - 任何坏注册条都要变成 invalid
            self.valid = False
            self.error = f"注册条解析失败: {exc}"
            return

        def _get(key, default):
            return data.get(key, default)

        raw_path = _get("path", None)
        if not isinstance(raw_path, str) or not raw_path.strip():
            self.valid = False
            self.error = "缺少 path 字段（服务真实目录）"
            return
        cand = Path(raw_path.strip())
        if not cand.is_absolute():
            cand = (reg_path.parent / cand).resolve()
        self.path = cand.resolve()
        if not self.path.is_dir():
            self.valid = False
            self.error = f"path 不是存在的目录: {self.path}"
            return

        self.enabled = bool(_get("enabled", True))
        self.display = str(_get("display", name))
        self.description = str(_get("description", ""))
        self.entry = str(_get("entry", "")).strip()
        args = _get("args", [])
        self.args = [str(a) for a in args] if isinstance(args, list) else []
        self.cwd = str(_get("cwd", "."))
        self.mode = str(_get("mode", "managed")).lower()
        self.autostart = bool(_get("autostart", False))

        if isinstance(data.get("env"), dict):
            self.env = {str(k): str(v) for k, v in data["env"].items()}
        u = data.get("url")
        if isinstance(u, str) and u.strip():
            self.url = u.strip()
        if isinstance(data.get("stop_script"), str) and data["stop_script"]:
            self.stop_script = data["stop_script"]
        if isinstance(data.get("pidfile"), str) and data["pidfile"]:
            self.pidfile = data["pidfile"]

        health = data.get("health") or {}
        if not isinstance(health, dict):
            health = {}
        self.health_type = str(health.get("type", "none")).lower()
        try:
            self.health_port = int(health["port"]) if "port" in health else None
        except (TypeError, ValueError):
            self.health_port = None
        self.health_url = health.get("url")
        try:
            self.health_timeout = float(health.get("timeout_sec", 20.0))
        except (TypeError, ValueError):
            self.health_timeout = 20.0

        # ---- 结构校验 ----
        problems = []
        if self.mode not in ("managed", "detached"):
            problems.append(f"mode 仅支持 managed/detached，得到 {self.mode!r}")
        if not self.entry:
            problems.append("缺少 entry 字段")
        if self.health_type not in ("none", "port", "http", "process"):
            problems.append(f"health.type 未知: {self.health_type!r}")
        elif self.health_type == "port" and self.health_port is None:
            problems.append("health.type=port 需要 health.port")
        elif self.health_type == "http" and not self.health_url:
            problems.append("health.type=http 需要 health.url")
        if self.mode == "detached":
            if not self.stop_script and not self.pidfile:
                problems.append("detached 模式需要 stop_script（或 pidfile）承担停止职责")
            if self.health_type == "process":
                problems.append("detached 不支持 process 型健康检查（无常驻进程句柄），请用 port/http")
        if not self.entry_abs.is_file():
            problems.append(f"入口文件不存在: {self.entry_abs}")

        if problems:
            self.valid = False
            self.error = "；".join(problems)

    # ---- 相对 path 的解析；转接层（adapters\<svc>\）优先，官方/原生脚本回退 ----
    def _resolve(self, rel: str) -> Path:
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


class Scanner:
    """定期重扫 reg\\*.json——注册中心是唯一发现源。"""

    def __init__(self, reg_dir: Path):
        self.reg_dir = reg_dir.resolve()
        self.adapter_dir = reg_dir.parent / "adapters"

    def scan(self) -> dict[str, Spec]:
        specs: dict[str, Spec] = {}
        if not self.reg_dir.is_dir():
            return specs
        for f in sorted(self.reg_dir.glob("*.json")):
            spec = Spec(f.stem, f, self.adapter_dir)
            specs[spec.folder] = spec
        return specs