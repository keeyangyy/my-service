"""my-service · core/host.py

宿主自身生命周期控制器——独立于服务列表的专属通道（规避自举悖论）。

宿主不写自己的注册条（否则会出现"编排者杀自己 / 自启套娃"），因此面板的
「重启宿主 / 停止宿主」走 `/api/host/*` 独立端点，而不是服务通道。本模块只做
两件事：延迟半拍执行动作、重启时派生一个脱离本进程的新宿主。

版本号 `VERSION` 属于宿主自身，放在这里；server.py 与面板 header 共用。
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from . import audit

VERSION = "0.13.0"

CORE_DIR = Path(__file__).resolve().parent          # core/
SRC_DIR = CORE_DIR.parent                            # src/
SERVER_PY = SRC_DIR / "server.py"
_DETACH = 0x00000008 | 0x00000200   # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP


class HostController:
    """宿主自身生命周期控制器——独立于服务列表的专属通道（规避自举悖论）。"""

    def __init__(self, manager, logs_dir: Path, data_dir: Path,
                 app_dir: Path | None = None, home: Path | None = None):
        self.manager = manager
        self.logs_dir = logs_dir    # 记录：登记簿 host.jsonl / 异常栈 host.err.log
        self.data_dir = data_dir    # 数据：host.pid 等运行态
        self.app_dir = Path(app_dir) if app_dir else CORE_DIR.parent.parent
        self.home = Path(home) if home else self.app_dir
        self.started = time.time()
        self.httpd = None   # serve_forever 前由 main 注入
        self.autostart = self._detect_autostart()

    @staticmethod
    def _startup_cmd_path() -> Path | None:
        """启动文件夹里的自启 cmd（无需管理员权限的那一种）。"""
        appdata = os.environ.get("APPDATA")
        if not appdata:
            return None
        return (Path(appdata) / "Microsoft" / "Windows" / "Start Menu"
                / "Programs" / "Startup" / "start-my-service.cmd")

    @staticmethod
    def _detect_autostart() -> str:
        """探测本机自启方式：startup（启动文件夹）/ logon（计划任务）/ none。

        构造时探测一次即可——自启项变化需要重装，不需要每秒查。
        """
        cmd = HostController._startup_cmd_path()
        if cmd is not None and cmd.is_file():
            return "startup"
        try:
            done = subprocess.run(["schtasks", "/query", "/tn", "StartMyService"],
                                  capture_output=True, timeout=3)
            if done.returncode == 0:
                return "logon"
        except (OSError, subprocess.SubprocessError):
            pass
        return "none"

    def _launcher_line(self) -> str:
        """自启时执行的命令：便携包用包根 start.cmd，源码模式走 run/start.ps1。"""
        portable = self.home != self.app_dir and (self.home / "start.cmd").is_file()
        if portable:
            return f'start "" /min "{self.home / "start.cmd"}"'
        run_ps = self.app_dir / "run" / "start.ps1"
        return ('start "" /min pwsh -NoProfile -WindowStyle Hidden '
                f'-ExecutionPolicy Bypass -File "{run_ps}" start')

    def set_autostart(self, enabled: bool) -> tuple[bool, str]:
        """写/删启动文件夹自启项（面板开关用）→ (ok, 当前状态)。

        只处理启动文件夹这一种：不需要管理员权限。计划任务 / SYSTEM / Nssm
        属于高级模式，仍走 run/install_autostart.ps1。
        """
        path = self._startup_cmd_path()
        if path is None:
            return False, "缺少 APPDATA 环境变量"
        try:
            if enabled:
                path.parent.mkdir(parents=True, exist_ok=True)
                content = "@echo off\r\n" + self._launcher_line() + "\r\n"
                path.write_bytes(content.encode("gbk"))
            else:
                path.unlink(missing_ok=True)
        except OSError as exc:
            return False, f"写自启项失败: {exc}"
        self.autostart = "startup" if enabled else "none"
        return True, self.autostart

    def meta(self) -> dict:
        return {
            "ok": True, "pid": os.getpid(), "version": VERSION,
            "uptime_sec": int(time.time() - self.started),
            "started_at": datetime.fromtimestamp(
                self.started, tz=timezone.utc).isoformat(),
            "autostart": self.autostart,
        }

    def schedule(self, action: str) -> None:
        threading.Timer(0.5, self._run, args=(action,)).start()

    def _spawn_replacement(self) -> None:
        launcher = self.home / "start.cmd"
        if self.home != self.app_dir and launcher.is_file():
            # 便携包：由 launcher 重启——它会先替换待生效的 app/，再起 python
            subprocess.Popen(
                [str(launcher)], cwd=str(self.home),
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, creationflags=_DETACH, close_fds=True,
            )
            return
        subprocess.Popen(
            [sys.executable, str(SERVER_PY)], cwd=str(SRC_DIR),
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, creationflags=_DETACH, close_fds=True,
        )

    def _run(self, action: str) -> None:
        try:
            if action == "restart":
                self._spawn_replacement()   # 新进程会重试绑定，等本进程释放端口
            audit.emit("host_stop", note=f"面板/API 发起 {action}（被管服务不受影响，继续运行）")
        except Exception:  # noqa: BLE001
            pass
        os._exit(0)                          # 立即退出，释放 1236
