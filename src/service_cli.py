"""my-service 启停引擎（唯一实现）。

`run/*.ps1` 与便携包的 `start.cmd` / `stop.cmd` 都只是薄壳，调本模块的子命令；
启停逻辑只在这里维护一份，并且可以被单测直接覆盖（tests/test_service_cli.py）。

用法：
    python src/service_cli.py start|stop|restart|status|shortcut|update-apply

架构位置：
    app/            代码根（本文件在 app/src/ 下）
    data_root/      数据根：MY_SERVICE_HOME 优先；便携包取 app 的上一级；否则 = app
"""
from __future__ import annotations

import argparse
import contextlib
import ctypes
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent      # app/
CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 1236
START_WAIT_TENTHS = 30          # 等待宿主写 host.pid 的上限（3 秒）


def resolve_data_root(app: Path) -> Path:
    """数据根：MY_SERVICE_HOME → 便携包（上一级有 runtime/python.exe）→ 源码模式 = app。

    与 server._resolve_home 同语义；这里独立实现，避免 CLI 依赖宿主装配链。
    """
    env = os.environ.get("MY_SERVICE_HOME")
    if env:
        return Path(env).expanduser().resolve()
    if (app.parent / "runtime" / "python.exe").is_file():
        return app.parent
    return app


def interpreter(app: Path, data_root: Path) -> Path:
    """宿主解释器：优先 pythonw（GUI 子系统，无控制台），便携包用 runtime 里的。"""
    if data_root != app:
        portable = data_root / "runtime" / "pythonw.exe"
        if portable.is_file():
            return portable
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    if pythonw.is_file():
        return pythonw
    return Path(sys.executable)


def pid_file(data_root: Path) -> Path:
    return Path(data_root) / "data" / "host.pid"


def read_pid(data_root: Path) -> int | None:
    try:
        pid = int(pid_file(data_root).read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        return None
    return pid if pid > 0 else None


def pid_alive(pid: int) -> bool:
    """OpenProcess 探测（不借子进程；等价 ps1 的 Get-Process -Id）。"""
    try:
        handle = ctypes.windll.kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    except (AttributeError, OSError):
        return False
    if not handle:
        return False
    ctypes.windll.kernel32.CloseHandle(handle)
    return True


def is_running(data_root: Path) -> int | None:
    pid = read_pid(data_root)
    if pid and pid_alive(pid):
        return pid
    return None


def load_config(data_root: Path) -> dict:
    cfg = {"host": DEFAULT_HOST, "port": DEFAULT_PORT}
    path = Path(data_root) / "config.json"
    if not path.is_file():
        return cfg
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return cfg
    if isinstance(data, dict):
        cfg.update({k: data[k] for k in ("host", "port") if k in data})
    return cfg


def version(app: Path) -> str:
    """从 core/host.py 读 VERSION（不 import，避免牵连宿主模块）。"""
    try:
        text = (Path(app) / "src" / "core" / "host.py").read_text(encoding="utf-8")
    except OSError:
        return "?"
    found = re.search(r'^VERSION\s*=\s*"([^"]+)"', text, re.MULTILINE)
    return found.group(1) if found else "?"


def shortcut_path(app: Path, data_root: Path) -> Path:
    """面板快捷方式：源码模式放 app/run/；便携包放包根（升级替换 app/ 时不丢）。"""
    if Path(data_root) == Path(app):
        return Path(app) / "run" / "Dashboard.url"
    return Path(data_root) / "Dashboard.url"


def write_shortcut(app: Path, data_root: Path) -> Path:
    path = shortcut_path(app, data_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    cfg = load_config(data_root)
    url = f"http://{cfg['host']}:{cfg['port']}"
    path.write_bytes(f"[InternetShortcut]\r\nURL={url}\r\n".encode("ascii"))
    return path


def ensure_shortcut(app: Path, data_root: Path) -> None:
    if not shortcut_path(app, data_root).is_file():
        write_shortcut(app, data_root)


def cmd_start(app: Path, data_root: Path) -> int:
    existing = is_running(data_root)
    if existing:
        print(f"已在运行 pid={existing}")
        return 0
    for name in ("data", "data/logs"):
        (Path(data_root) / name).mkdir(parents=True, exist_ok=True)
    subprocess.Popen(
        [str(interpreter(app, data_root)), str(Path(app) / "src" / "server.py")],
        cwd=str(Path(app) / "src"),
        creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
        close_fds=True, stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    for _ in range(START_WAIT_TENTHS):
        time.sleep(0.1)
        pid = is_running(data_root)
        if pid:
            print(f"started pid={pid}")
            ensure_shortcut(app, data_root)
            return 0
    print("FAILED — 查看 data/logs/host.err.log 或 data/logs/host.jsonl", file=sys.stderr)
    return 1


def cmd_stop(app: Path, data_root: Path) -> int:
    pid = is_running(data_root)
    if not pid:
        print("未在运行")
        return 0
    subprocess.run(["taskkill", "/PID", str(pid), "/F"],
                   capture_output=True, creationflags=CREATE_NO_WINDOW)
    with contextlib.suppress(OSError):
        pid_file(data_root).unlink(missing_ok=True)
    print(f"stopped pid={pid}")
    return 0


def cmd_restart(app: Path, data_root: Path) -> int:
    code = cmd_stop(app, data_root)
    if code != 0:
        return code
    time.sleep(0.6)
    return cmd_start(app, data_root)


def cmd_status(app: Path, data_root: Path) -> int:
    cfg = load_config(data_root)
    url = f"http://{cfg['host']}:{cfg['port']}"
    pid = is_running(data_root)
    if pid:
        print(f"running pid={pid} v{version(app)} {url}")
        return 0
    print(f"stopped v{version(app)} {url}")
    return 1


def cmd_shortcut(app: Path, data_root: Path) -> int:
    path = write_shortcut(app, data_root)
    print(f"已生成面板快捷方式: {path}")
    return 0


def cmd_update_apply(app: Path, data_root: Path) -> int:
    """应用暂存的升级（包根 start.cmd 在起宿主前调用，可用性窗口唯一）。

    便携包：把 data/update/code.new 按清单替换 src/web/run/docs + 三文档，
    旧代码留 data/bak/code-prev/ 可回滚。没有待生效升级时静默成功。
    """
    from core import update

    if not (Path(data_root) / update.UPDATE_SUBDIR / "code.new").is_dir():
        return 0
    result = update.apply_pending(data_root)
    if result.get("ok"):
        print(f"已升级到 v{result.get('version')}"
              f"（旧代码备份: {result.get('backup')}）")
        return 0
    print(f"升级未生效: {result.get('error')}", file=sys.stderr)
    return 1


HANDLERS = {
    "start": cmd_start,
    "stop": cmd_stop,
    "restart": cmd_restart,
    "status": cmd_status,
    "shortcut": cmd_shortcut,
    "update-apply": cmd_update_apply,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="my-service", description="my-service 启停入口")
    parser.add_argument("action", nargs="?", default="start", choices=sorted(HANDLERS))
    args = parser.parse_args(argv)
    app = APP_DIR
    return HANDLERS[args.action](app, resolve_data_root(app))


if __name__ == "__main__":
    raise SystemExit(main())
