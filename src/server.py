# -*- coding: utf-8 -*-
"""my-service 宿主入口。

    python src/server.py            # 前台运行
    entry\\start.ps1               # 后台托管（推荐）

装配顺序：config.json → Scanner → Manager(含 monitor 线程) → HTTP API。
自启动：autostart=true 且 enabled 的服务在宿主起来后自动 start。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SRC_DIR))

from core import api  # noqa: E402
from core.scanner import Scanner  # noqa: E402
from core.state import Manager  # noqa: E402

CONFIG_PATH = Path(__file__).resolve().parents[1] / "config.json"
SERVER_PY = SRC_DIR / "server.py"
VERSION = "1.0"

_DETACH = 0x00000008 | 0x00000200   # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP


class HostController:
    """宿主自身生命周期控制器——独立于服务列表的专属通道（规避自举悖论）。"""

    def __init__(self, manager: Manager, rdir: Path):
        self.manager = manager
        self.rdir = rdir
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
            self.manager.stop_all()          # 收割所有被管子服务
        except Exception:  # noqa: BLE001
            pass
        os._exit(0)                          # 立即退出，释放 1236


def load_config() -> dict:
    defaults = {
        "host": "127.0.0.1",
        "port": 1236,
        "services_root": "..",
        "ignore_dirs": [],
        "log_dir": "logs",
        "log_max_bytes": 20_000_000,
    }
    if CONFIG_PATH.is_file():
        try:
            user = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
            if isinstance(user, dict):
                defaults.update(user)
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] config.json 解析失败({exc})，使用默认配置")
    return defaults


def _setup_host_logging(base: Path) -> None:
    """宿主自身日志遵循两档模型约定：logs\\service.log / service.err.log。
    只记录 my-service 的启停事件（监听地址/autostart 记录/崩溃栈），
    被管理服务的输出写它们各自的 logs\\ 目录（见 runner.start_process）。"""
    rdir = base / "logs"
    rdir.mkdir(parents=True, exist_ok=True)
    sys.stdout = open(rdir / "service.log", "a", buffering=1,
                      encoding="utf-8", errors="replace")
    sys.stderr = open(rdir / "service.err.log", "a", buffering=1,
                      encoding="utf-8", errors="replace")


def main() -> int:
    cfg = load_config()
    # base = my-service 根（src/ 的上一级）；宿主自身日志只记录自己的启停事件
    base = Path(__file__).resolve().parents[1]
    services_root = (base / cfg["services_root"]).resolve()
    _setup_host_logging(base)

    scanner = Scanner(services_root, cfg.get("ignore_dirs", []),
                      exclude_paths=[base])
    manager = Manager(scanner)
    controller = HostController(manager, base / "logs")

    threading.Thread(target=manager.monitor_loop, daemon=True,
                     name="monitor").start()

    httpd = api.serve(manager, cfg["host"], int(cfg["port"]), controller=controller)
    controller.httpd = httpd
    autostart_names = [n for n, s in manager.specs.items()
                       if s.valid and s.enabled and s.autostart]
    print(f"my-service v{VERSION} listening on http://{cfg['host']}:{cfg['port']}"
          f"  (pid={os.getpid()})", flush=True)
    print(f"services_root = {services_root}  |  发现服务: "
          f"{', '.join(sorted(manager.specs)) or '(无)'}", flush=True)

    # 延迟半拍先让 HTTP 就绪，再按 manifest 声明自动拉起
    def _auto_start():
        time.sleep(0.5)
        for name in sorted(autostart_names):
            ok, msg = manager.start(name)
            print(f"[autostart] {name}: {msg}", flush=True)

    threading.Thread(target=_auto_start, daemon=True).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nCtrl+C 收到，正在收割子进程…", flush=True)
        manager.stop_all()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
