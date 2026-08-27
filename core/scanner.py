# -*- coding: utf-8 -*-
"""my-service · core/scanner.py

扫描 services_root 下的 */manifest.json，把每个服务文件夹解析成 Spec。
铁律：本框架不含任何服务业务源码；一切服务认知来自 manifest.json 协议。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


class Spec:
    """单个服务的声明（解析后的 manifest）。

    valid=False 时 error 字段携带原因，前端以 invalid 状态展示，不参与编排。
    """

    def __init__(self, folder: str, manifest_path: Path):
        self.folder = folder
        self.manifest_path = manifest_path
        self.raw: dict = {}
        self.valid: bool = True
        self.error: str | None = None

        # ---- 必备字段 ----
        self.enabled: bool = True
        self.display: str = folder
        self.description: str = ""
        self.entry: str = ""
        self.args: list[str] = []
        self.cwd: str = "."
        self.mode: str = "managed"            # managed | detached(v2 未实现)
        self.health_type: str = "none"        # none | port | http | process
        self.health_port: int | None = None
        self.health_url: str | None = None
        self.health_timeout: float = 20.0
        self.env: dict[str, str] = {}
        self.autostart: bool = False
        self.stop_script: str | None = None
        self.pidfile: str | None = None       # detached 模式预留

        try:
            text = manifest_path.read_text(encoding="utf-8-sig")
            data = json.loads(text)
            if not isinstance(data, dict):
                raise ValueError("manifest 根节点必须是 JSON 对象")
            self.raw = data
        except Exception as exc:  # noqa: BLE001 - 任何坏 manifest 都要变成 invalid
            self.valid = False
            self.error = f"manifest.json 解析失败: {exc}"
            return

        def _get(key, default):
            return data.get(key, default)

        self.enabled = bool(_get("enabled", True))
        self.display = str(_get("display", folder))
        self.description = str(_get("description", ""))
        self.entry = str(_get("entry", "")).strip()
        args = _get("args", [])
        self.args = [str(a) for a in args] if isinstance(args, list) else []
        self.cwd = str(_get("cwd", "."))
        self.mode = str(_get("mode", "managed")).lower()
        self.autostart = bool(_get("autostart", False))

        if isinstance(data.get("env"), dict):
            self.env = {str(k): str(v) for k, v in data["env"].items()}
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
        if not self.entry and self.mode == "managed":
            problems.append("缺少 entry 字段")
        if self.health_type not in ("none", "port", "http", "process"):
            problems.append(f"health.type 未知: {self.health_type!r}")
        elif self.health_type == "port" and self.health_port is None:
            problems.append("health.type=port 需要 health.port")
        elif self.health_type == "http" and not self.health_url:
            problems.append("health.type=http 需要 health.url")
        if self.mode == "detached" and not self.pidfile:
            problems.append("detached 模式当前版本未实现（需 pidfile，v2 支持）")

        # 端口冲突提示交给 Manager 全局判定（跨服务检查）
        if problems:
            self.valid = False
            self.error = "；".join(problems)

    @property
    def workdir(self) -> Path:
        return (self.manifest_path.parent / self.cwd).resolve()

    @property
    def entry_abs(self) -> Path:
        return (self.manifest_path.parent / self.entry).resolve()

    @property
    def stop_script_abs(self) -> Path | None:
        if not self.stop_script:
            return None
        return (self.manifest_path.parent / self.stop_script).resolve()


class Scanner:
    """按 config 定期重扫磁盘。服务注册 = 放一个带 manifest.json 的文件夹。"""

    def __init__(self, root: Path, ignore_dirs: list[str], log_dir_name: str = "logs"):
        self.root = root.resolve()
        self.ignore_dirs = set(ignore_dirs) | {log_dir_name}
        # my-service 自身永远被忽略（无论文件夹叫什么）
        self.self_dir = Path(__file__).resolve().parent.parent

    def scan(self) -> dict[str, Spec]:
        specs: dict[str, Spec] = {}
        if not self.root.is_dir():
            return specs
        for child in sorted(self.root.iterdir()):
            if not child.is_dir():
                continue
            if child.name in self.ignore_dirs or child.name.startswith("."):
                continue
            if self.self_dir and child.resolve() == self.self_dir:
                continue
            manifest = child / "manifest.json"
            if not manifest.is_file():
                continue  # 不是服务的普通文件夹，静默跳过
            spec = Spec(child.name, manifest)
            specs[spec.folder] = spec
        return specs
