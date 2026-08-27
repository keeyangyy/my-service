# -*- coding: utf-8 -*-
"""my-service 宿主入口。

    python server.py            # 前台运行
    pwsh -File run.ps1          # 后台托管运行（推荐）

装配顺序：config.json → Scanner → Manager(含 monitor 线程) → HTTP API。
自启动：autostart=true 且 enabled 的服务在宿主起来后自动 start。
"""
from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

from core import api  # noqa: E402
from core.scanner import Scanner  # noqa: E402
from core.state import Manager  # noqa: E402

CONFIG_PATH = BASE / "config.json"


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


def _setup_host_logging(log_root: Path) -> None:
    """宿主 stdout/err 落盘。避免 Start-Process 重定向造成的句柄跨层绑定问题。"""
    log_root.mkdir(parents=True, exist_ok=True)
    sys.stdout = open(log_root / "host.out.log", "a", buffering=1,
                      encoding="utf-8", errors="replace")
    sys.stderr = open(log_root / "host.err.log", "a", buffering=1,
                      encoding="utf-8", errors="replace")


def main() -> int:
    cfg = load_config()
    base = Path(__file__).resolve().parent
    services_root = (base / cfg["services_root"]).resolve()
    log_root = (base / cfg["log_dir"]).resolve()
    _setup_host_logging(log_root)

    scanner = Scanner(services_root, cfg.get("ignore_dirs", []), cfg["log_dir"])
    manager = Manager(scanner, log_root, int(cfg["log_max_bytes"]))

    threading.Thread(target=manager.monitor_loop, daemon=True,
                     name="monitor").start()

    httpd = api.serve(manager, cfg["host"], int(cfg["port"]))
    autostart_names = [n for n, s in manager.specs.items()
                       if s.valid and s.enabled and s.autostart]
    print(f"my-service listening on http://{cfg['host']}:{cfg['port']}", flush=True)
    print(f"services_root = {services_root}  |  发现服务: "
          f"{', '.join(sorted(manager.specs)) or '(无)'}", flush=True)

    # 延迟半拍先让 HTTP 就绪，再按 manifest 声明自动拉起
    def _auto_start():
        import time
        time.sleep(0.5)
        for name in sorted(autostart_names):
            ok, msg = manager.start(name)
            print(f"[autostart] {name}: {msg}", flush=True)

    threading.Thread(target=_auto_start, daemon=True).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nCtrl+C 收到，正在收割子进程…", flush=True)
        for name in list(manager.handles):
            manager.stop(name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
