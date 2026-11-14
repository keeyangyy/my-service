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

    def __init__(self, manager, logs_dir: Path, data_dir: Path):
        self.manager = manager
        self.logs_dir = logs_dir    # 记录：登记簿 host.jsonl / 异常栈 host.err.log
        self.data_dir = data_dir    # 数据：host.pid 等运行态
        self.started = time.time()
        self.httpd = None   # serve_forever 前由 main 注入

    def meta(self) -> dict:
        return {
            "ok": True, "pid": os.getpid(), "version": VERSION,
            "uptime_sec": int(time.time() - self.started),
            "started_at": datetime.fromtimestamp(
                self.started, tz=timezone.utc).isoformat(),
        }

    def schedule(self, action: str) -> None:
        threading.Timer(0.5, self._run, args=(action,)).start()

    def _spawn_replacement(self) -> None:
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
