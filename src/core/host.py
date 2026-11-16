"""my-service · core/host.py

宿主自身生命周期控制器——独立于服务列表的专属通道（规避自举悖论）。

宿主不写自己的注册条（否则会出现"编排者杀自己 / 自启套娃"），因此面板的
「重启宿主 / 停止宿主」走 `/api/host/*` 独立端点，而不是服务通道。本模块只做
两件事：延迟半拍执行动作、重启时派生一个脱离本进程的新宿主。

版本号 `VERSION` 属于宿主自身，放在这里；server.py 与面板 header 共用。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from . import audit, update

VERSION = "0.20.0"

CORE_DIR = Path(__file__).resolve().parent          # core/
SRC_DIR = CORE_DIR.parent                            # src/
SERVER_PY = SRC_DIR / "server.py"
_DETACH = 0x00000008 | 0x00000200   # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
_NO_WINDOW = 0x08000000             # CREATE_NO_WINDOW：框架发起的控制台程序不弹黑窗


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
    def _startup_dir() -> Path | None:
        appdata = os.environ.get("APPDATA")
        if not appdata:
            return None
        return (Path(appdata) / "Microsoft" / "Windows" / "Start Menu"
                / "Programs" / "Startup")

    @classmethod
    def _startup_lnk_path(cls) -> Path | None:
        """启动文件夹里的自启快捷方式（.lnk 指向 pythonw，登录时无窗口）。"""
        base = cls._startup_dir()
        return base / "start-my-service.lnk" if base else None

    @classmethod
    def _startup_vbs_path(cls) -> Path | None:
        """历史方案：wscript 拉起的 vbs（保留路径用于检测与清理）。"""
        base = cls._startup_dir()
        return base / "start-my-service.vbs" if base else None

    @classmethod
    def _startup_cmd_path(cls) -> Path | None:
        """历史方案留下的 cmd（保留路径用于检测与清理）。"""
        base = cls._startup_dir()
        return base / "start-my-service.cmd" if base else None

    @staticmethod
    def _detect_autostart() -> str:
        """探测本机自启方式：startup（启动文件夹 lnk/vbs/cmd）/ logon（计划任务）/ none。

        构造时探测一次即可——自启项变化需要重装，不需要每秒查。
        """
        for path in (HostController._startup_lnk_path(),
                     HostController._startup_vbs_path(),
                     HostController._startup_cmd_path()):
            if path is not None and path.is_file():
                return "startup"
        try:
            done = subprocess.run(["schtasks", "/query", "/tn", "StartMyService"],
                                  capture_output=True, timeout=3,
                                  creationflags=_NO_WINDOW)
            if done.returncode == 0:
                return "logon"
        except (OSError, subprocess.SubprocessError):
            pass
        return "none"

    def _autostart_launcher(self) -> tuple[Path, Path]:
        r"""自启用的（解释器, 入口）：优先 pythonw（GUI 子系统，无控制台窗口）。

        `-WithRuntime` 打包时包根有 runtime\pythonw.exe，优先用它；否则用系统 pythonw。
        """
        script = self.app_dir / "src" / "server.py"
        portable = self.app_dir / "runtime" / "pythonw.exe"
        if portable.is_file():
            return portable, script
        pythonw = Path(sys.executable).with_name("pythonw.exe")
        if pythonw.is_file():
            return pythonw, script
        return Path(sys.executable), script

    @staticmethod
    def _ps_quote(text: str) -> str:
        """PowerShell 单引号字面量（内部单引号翻倍）。"""
        return "'" + text.replace("'", "''") + "'"

    def _write_lnk(self, lnk: Path) -> None:
        """用系统自带的 powershell.exe 生成 .lnk（目标 pythonw.exe，登录时无窗口）。

        pythonw 是 GUI 子系统、没有控制台，所以快捷方式不需要任何隐藏窗口技巧；
        相比 vbs 少一层 wscript，用户右键属性就能看清目标。
        """
        exe, script = self._autostart_launcher()
        ps = (
            "$w = New-Object -ComObject WScript.Shell; "
            f"$s = $w.CreateShortcut({self._ps_quote(str(lnk))}); "
            f"$s.TargetPath = {self._ps_quote(str(exe))}; "
            f"$s.Arguments = {self._ps_quote(chr(34) + str(script) + chr(34))}; "
            f"$s.WorkingDirectory = {self._ps_quote(str(script.parent))}; "
            f"$s.IconLocation = {self._ps_quote(str(exe) + ',0')}; "
            "$s.Description = 'my-service 宿主（开机自启，无窗口）'; "
            "$s.Save()"
        )
        # Windows 自带的 Windows PowerShell 5.1 在 PATH 里；被裁剪时退回裸名字再解析一次
        ps_exe = shutil.which("powershell.exe") or "powershell.exe"
        subprocess.run(
            [ps_exe,
             "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-Command", ps],
            capture_output=True, timeout=30, check=True,
            creationflags=_NO_WINDOW,
        )

    def set_autostart(self, enabled: bool) -> tuple[bool, str]:
        """写/删启动文件夹自启项（面板开关用）→ (ok, 当前状态)。

        自启项是 `.lnk` 快捷方式，指向 pythonw.exe + src\\server.py：pythonw 无控制台，
        登录时不会闪黑窗，也不依赖 pwsh/wscript 这类脚本宿主。
        只处理启动文件夹（无需管理员权限）；计划任务 / SYSTEM / Nssm 属于高级模式，
        仍走 run/install_autostart.ps1。
        """
        lnk = self._startup_lnk_path()
        if lnk is None:
            return False, "缺少 APPDATA 环境变量"
        legacy = [p for p in (self._startup_vbs_path(),
                              self._startup_cmd_path()) if p is not None]
        try:
            if enabled:
                lnk.parent.mkdir(parents=True, exist_ok=True)
                self._write_lnk(lnk)
            else:
                lnk.unlink(missing_ok=True)
            for path in legacy:
                path.unlink(missing_ok=True)   # 清掉历史 vbs/cmd 方案，避免双启动
        except (OSError, subprocess.SubprocessError) as exc:
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
        """派生新宿主。有暂存升级时先拉一个更新器，等本进程退出再替换代码。

        面板「更新并重启」= `/api/update/apply` 下载暂存 → 本进程派生更新器 → 退出；
        更新器（`service_cli.py apply-update-and-start`）等旧 pid 消失后替换
        `src/ web/ run/ docs/` 与三个文档，再起新宿主——不依赖 start.cmd / 包内 Python。
        """
        if update.pending_version(self.home):
            # cwd 不能是 src：更新器要移动/删除 src，Windows 下操作进程 cwd 会失败
            subprocess.Popen(
                [sys.executable, str(SRC_DIR / "service_cli.py"),
                 "apply-update-and-start"],
                cwd=str(self.home),
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
