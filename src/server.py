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

from core import api, audit, runner  # noqa: E402
from core.scanner import Scanner  # noqa: E402
from core.scheduler import Scheduler  # noqa: E402
from core.state import Manager  # noqa: E402

CONFIG_PATH = Path(__file__).resolve().parents[1] / "config.json"
SERVER_PY = SRC_DIR / "server.py"
VERSION = "0.5.0"

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
            audit.emit("host_stop", note=f"面板/API 发起 {action}（被管服务不受影响，继续运行）")
        except Exception:  # noqa: BLE001
            pass
        os._exit(0)                          # 立即退出，释放 1236


def load_config() -> dict:
    defaults = {
        "host": "127.0.0.1",
        "port": 1236,
        "reg_dir": "reg",
    }
    if CONFIG_PATH.is_file():
        try:
            user = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
            if isinstance(user, dict):
                defaults.update(user)
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] config.json 解析失败({exc})，使用默认配置")
    return defaults


def _setup_host_logging(base: Path) -> Path:
    """宿主自身的日志边界（被管服务的输出零接管，见 core/audit.py 顶部说明）。

    - sys.stderr → logs\\host.err.log：异常栈留档，兼作无控制台场景的兜底。
    - sys.stdout：终端直跑时保留可见；后台/计划任务（S4U 无控制台）时丢到 devnull。
      不再重定向到文件——启停事件属于登记簿 host.jsonl，stdout 不冒充日志。
    """
    rdir = base / "logs"
    rdir.mkdir(parents=True, exist_ok=True)
    sys.stderr = open(rdir / "host.err.log", "a", buffering=1,
                      encoding="utf-8", errors="replace")
    try:
        on_console = bool(sys.stdout) and sys.stdout.isatty()
    except (OSError, ValueError):
        on_console = False
    if not on_console:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    return rdir


def main() -> int:
    cfg = load_config()
    # base = my-service 根（src/ 的上一级）；框架侧运行时产物全部收在 base\logs 内
    base = Path(__file__).resolve().parents[1]
    rdir = _setup_host_logging(base)
    audit_path = audit.init(rdir)
    runner.set_runtime_dir(rdir)
    try:
        (rdir / "host.pid").write_text(str(os.getpid()), encoding="ascii")
    except OSError:
        pass
    scanner = Scanner((base / cfg["reg_dir"]).resolve())
    manager = Manager(scanner)
    # 定时任务注册目录（reg\cron\）：不存在则建空目录，用户一眼可见往哪放注册条
    cron_dir = (base / cfg["reg_dir"] / "cron").resolve()
    cron_dir.mkdir(parents=True, exist_ok=True)
    scheduler = Scheduler(cron_dir, rdir / "cron_state.json")
    controller = HostController(manager, rdir)

    httpd = api.serve(manager, cfg["host"], int(cfg["port"]),
                      controller=controller, scheduler=scheduler)
    controller.httpd = httpd
    autostart_names = [n for n, s in manager.specs.items()
                       if s.valid and s.enabled and s.autostart]
    listing = ", ".join(sorted(manager.specs)) or "(无)"
    cron_names = ", ".join(sorted(s["name"] for s in scheduler.snapshot())) or "(无)"
    audit.emit("host_start", pid=os.getpid(),
               note=f"v{VERSION} listening http://{cfg['host']}:{cfg['port']}"
                    f" | 注册服务: {listing} | 定时任务: {cron_names}")
    print(f"my-service v{VERSION} listening on http://{cfg['host']}:{cfg['port']}"
          f"  (pid={os.getpid()})", flush=True)
    print(f"reg = {scanner.reg_dir}  |  注册服务: {listing}  |  登记簿 = {audit_path}",
          flush=True)
    print(f"cron = {cron_dir}  |  定时任务: {cron_names}", flush=True)

    # monitor 与自启都在 host_start 之后才起：登记簿首行必须是宿主自己起来，
    # 否则 monitor 的 adopt_external 会抢在前面，事件因果倒置
    threading.Thread(target=manager.monitor_loop, daemon=True,
                     name="monitor").start()
    # 定时任务调度线程：同样在 host_start 之后（首条事件因果一致的同一约束）
    threading.Thread(target=scheduler.loop, daemon=True,
                     name="scheduler").start()

    # 延迟半拍先让 HTTP 就绪，再按 manifest 声明自动拉起
    # 防双开：自启前先判定是否已有实例在运行（外部/残留/手动），已在运行则跳过
    def _auto_start():
        time.sleep(0.5)
        for name in sorted(autostart_names):
            alive, reason = manager.external_alive(name)
            if alive:
                # 归位 running（登记簿记 autostart_skip，面板如实显示）
                manager.adopt_external(name, reason, act="autostart_skip")
                print(f"[autostart] {name}: 已在运行（{reason}），跳过自启并归位 running", flush=True)
                continue
            ok, msg = manager.start(name, via="autostart")
            print(f"[autostart] {name}: {msg}", flush=True)

    threading.Thread(target=_auto_start, daemon=True).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        audit.emit("host_stop", note="Ctrl+C 收到，被管服务不收割，宿主退出")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
