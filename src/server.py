"""my-service 宿主入口。

    python src/server.py            # 前台运行
    run\\start.ps1                 # 后台托管（推荐）

装配顺序：config.json → Scanner → Manager(含 monitor 线程) → HTTP API。
自启动：autostart=true 且 enabled 的服务在宿主起来后自动 start。
"""
from __future__ import annotations

import contextlib
import json
import os
import sys
import threading
import time
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SRC_DIR))

from core import api, audit, runner  # noqa: E402
from core.host import HostController, VERSION  # noqa: E402
from core.scanner import Scanner  # noqa: E402
from core.scheduler import Scheduler  # noqa: E402
from core.state import Manager  # noqa: E402

def _resolve_home(base: Path) -> Path:
    """数据根 home。

    - 显式 `MY_SERVICE_HOME` 优先（便于自定义数据位置）
    - 便携包：`base`（app/）的上一级有 `runtime/python.exe` → 数据放包根
    - 源码模式：home = base（行为与历史一致）
    """
    env = os.environ.get("MY_SERVICE_HOME")
    if env:
        return Path(env).expanduser().resolve()
    if (base.parent / "runtime" / "python.exe").is_file():
        return base.parent
    return base


def _config_path(home: Path) -> Path:
    return home / "config.json"


def load_config(home: Path) -> dict:
    defaults = {
        "host": "127.0.0.1",
        "port": 1236,
        "reg_dir": "reg",
        "audit_retention_days": 7,   # 登记簿事件保留天数（0 = 永不自动清理）
        "cors_local_only": True,     # 仅本机来源的网页可获得跨源许可（关掉=回到无 CORS 的历史行为）
        "backup": {"keep": 7},       # data/bak/ 保留份数（0 = 不自动清理）
    }
    cfg_path = _config_path(home)
    if cfg_path.is_file():
        try:
            user = json.loads(cfg_path.read_text(encoding="utf-8-sig"))
            if isinstance(user, dict):
                defaults.update(user)
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] config.json 解析失败({exc})，使用默认配置")
    return defaults


def _setup_host_logging(base: Path) -> tuple[Path, Path]:
    """宿主自身的日志边界（被管服务的输出零接管，见 core/audit.py 顶部说明）。

    返回 (logs_dir, data_dir)——**运行时数据全在 data\\ 下**（本项目约定）：
    - data\\logs\\  记录类：只追加、可随时删，删了不影响下次运行
    - data\\        数据类：运行态/状态/账本/备份，删了行为会变

    - sys.stderr → data\\logs\\host.err.log：异常栈留档，兼作无控制台场景的兜底。
    - sys.stdout：终端直跑时保留可见；后台/计划任务（S4U 无控制台）时丢到 devnull。
      不再重定向到文件——启停事件属于登记簿 host.jsonl，stdout 不冒充日志。
    """
    data_dir = base / "data"
    logs_dir = data_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)
    # 故意长期持有：宿主生命周期的 stderr 重定向，不能改成 with
    sys.stderr = open(logs_dir / "host.err.log", "a", buffering=1,  # noqa: SIM115
                      encoding="utf-8", errors="replace")
    try:
        on_console = bool(sys.stdout) and sys.stdout.isatty()
    except (OSError, ValueError):
        on_console = False
    if not on_console:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115 - 故意长开
    return logs_dir, data_dir


def main() -> int:
    # app = 代码根（src/ 的上一级）；home = 数据根（便携包时是 app 的上一级）
    app = Path(__file__).resolve().parents[1]
    home = _resolve_home(app)
    cfg = load_config(home)
    # 运行时数据都在 data\ 下：登记簿/异常栈进 data\logs\，运行态账本/pid 进 data\
    logs_dir, data_dir = _setup_host_logging(home)
    audit_path = audit.init(logs_dir)
    runner.set_runtime_dir(data_dir)
    with contextlib.suppress(OSError):
        (data_dir / "host.pid").write_text(str(os.getpid()), encoding="ascii")
    reg_root = (home / cfg["reg_dir"]).resolve()
    scanner = Scanner(reg_root / "services")
    monitor_settings = dict(cfg.get("monitor") or {})
    monitor_settings.setdefault("audit_retention_days",
                                cfg.get("audit_retention_days", 7))
    manager = Manager(scanner, settings=monitor_settings)
    # 备份保留策略：data/bak/ 只留最近 keep 份（0 = 不自动清理）
    backup_settings = dict(cfg.get("backup") or {})
    backup_settings.setdefault("keep", 7)
    # 定时任务注册目录（reg\cron\）：不存在则建空目录，用户一眼可见往哪放注册条
    cron_dir = (reg_root / "tasks").resolve()
    cron_dir.mkdir(parents=True, exist_ok=True)
    scheduler = Scheduler(cron_dir, data_dir / "cron_state.json",
                          tick_sec=float((cfg.get("scheduler") or {}).get("tick_sec", 10.0)))
    # 定时任务与服务不再按路径绑定：任务独立展示、独立管理
    controller = HostController(manager, logs_dir, data_dir, app_dir=app, home=home)

    httpd = api.serve(manager, cfg["host"], int(cfg["port"]),
                      controller=controller, scheduler=scheduler,
                      cors_local_only=bool(cfg.get("cors_local_only", True)),
                      settings_path=_config_path(home),
                      backup_settings=backup_settings)
    controller.httpd = httpd
    autostart_names = [n for n, s in manager.specs.items()
                       if s.valid and s.enabled and s.autostart]
    listing = ", ".join(sorted(manager.specs)) or "(无)"
    # 调度器只在自己循环里 scan；此处先扫一次，否则 host_start 横幅恒为"(无)"
    scheduler.scan()
    cron_names = ", ".join(sorted(s["name"] for s in scheduler.snapshot())) or "(无)"
    audit.emit("host_start", pid=os.getpid(),
               note=f"v{VERSION} listening http://{cfg['host']}:{cfg['port']}"
                    f" | 注册服务: {listing} | 定时任务: {cron_names}")
    print(f"my-service v{VERSION} listening on http://{cfg['host']}:{cfg['port']}"
          f"  (pid={os.getpid()})", flush=True)
    print(f"reg = {scanner.reg_dir}  |  注册服务: {listing}  |  登记簿 = {audit_path}",
          flush=True)
    print(f"cron = {cron_dir}  |  定时任务: {cron_names}", flush=True)

    # 启动接管：宿主重启后句柄表为空，先按身份把残留实例收敛（否则孤儿既显示
    # 不出 running、也停不掉、空闲也回收不了）。放在 host_start 之后，事件因果一致。
    adopted = manager.reconcile()
    if adopted:
        print("[reconcile] " + " | ".join(f"{k}: {v}" for k, v in adopted.items()),
              flush=True)

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
                print(f"[autostart] {name}: 已在运行（{reason}），跳过自启并归位 running",
                      flush=True)
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
