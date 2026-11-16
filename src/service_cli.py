"""my-service 启停引擎（唯一实现）。

`run/*.ps1` 与便携包的 `start.cmd` / `stop.cmd` 都只是薄壳，调本模块的子命令；
启停逻辑只在这里维护一份，并且可以被单测直接覆盖（tests/test_service_cli.py）。

用法：
    python src/service_cli.py start|stop|restart|status|shortcut|update-apply
    python src/service_cli.py ls|svc-start <名>|svc-stop <名>|svc-restart <名>
    python src/service_cli.py crons|cron-run <名>|events [名] [n]|host-err [n]
（服务管理命令走宿主 HTTP API，宿主未运行时直接报错退出）

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
import urllib.error
import urllib.parse
import urllib.request
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
    """数据根：MY_SERVICE_HOME → 否则 = app（包根与代码根同级）。

    与 server._resolve_home 同语义；这里独立实现，避免 CLI 依赖宿主装配链。
    """
    env = os.environ.get("MY_SERVICE_HOME")
    if env:
        return Path(env).expanduser().resolve()
    return app


def interpreter(app: Path, data_root: Path) -> Path:
    r"""宿主解释器：优先 pythonw（GUI 子系统，无控制台）。

    `-WithRuntime` 打包时包内自带 runtime\pythonw.exe，优先用它；否则用系统 pythonw。
    """
    portable = Path(app) / "runtime" / "pythonw.exe"
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


def client_host(host: str | None) -> str:
    """客户端连接用的地址：监听通配地址时改用回环。

    `0.0.0.0` / `::` 是「监听所有网卡」的意思，不能当作连接目标
    （Windows 上直接报 WinError 10049）。
    """
    text = (host or "").strip()
    if text in ("0.0.0.0", "::", "[::]"):
        return DEFAULT_HOST
    return text or DEFAULT_HOST


def base_url(data_root: Path) -> str:
    """宿主 API 基地址（读 config.json，通配监听自动回落回环）。"""
    cfg = load_config(data_root)
    return f"http://{client_host(cfg['host'])}:{cfg['port']}"


def version(app: Path) -> str:
    """从 core/host.py 读 VERSION（不 import，避免牵连宿主模块）。"""
    try:
        text = (Path(app) / "src" / "core" / "host.py").read_text(encoding="utf-8")
    except OSError:
        return "?"
    found = re.search(r'^VERSION\s*=\s*"([^"]+)"', text, re.MULTILINE)
    return found.group(1) if found else "?"


def shortcut_path(app: Path, data_root: Path) -> Path:
    r"""面板快捷方式落点：run\Dashboard.url（每台机器生成、不入库；升级替换 run/ 后会自动重建）。"""
    return Path(app) / "run" / "Dashboard.url"


def write_shortcut(app: Path, data_root: Path) -> Path:
    path = shortcut_path(app, data_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    url = base_url(data_root)
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
    url = base_url(data_root)
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


def cmd_apply_update_and_start(app: Path, data_root: Path) -> int:
    """面板「更新并重启」的落地动作：等旧宿主退出 → 应用升级 → 起新宿主。

    由旧宿主在退出前派生（见 core/host.py `_spawn_replacement`），因此能安全替换
    正被占用的代码目录；没有暂存升级时等价于普通重启。
    """
    from core import update

    old = read_pid(data_root)
    if old:
        deadline = time.time() + 30
        while time.time() < deadline and pid_alive(old):
            time.sleep(0.2)
        if pid_alive(old):
            print(f"旧宿主 pid={old} 未退出，放弃升级以免双实例", file=sys.stderr)
            return 1
    if (Path(data_root) / update.UPDATE_SUBDIR / "code.new").is_dir():
        result = update.apply_pending(data_root)
        if result.get("ok"):
            print(f"已应用升级 v{result.get('version')}"
                  f"（旧代码备份: {result.get('backup')}）")
        else:
            print(f"升级未生效: {result.get('error')}", file=sys.stderr)
    return cmd_start(app, data_root)


# ---------------------------------------------------------------- 服务管理（走宿主 HTTP API）
API_TIMEOUT = 8


def _api(data_root: Path, method: str, path: str, body: dict | None = None) -> dict:
    """调宿主 REST API；连不上时给出一句可操作的提示并退出。"""
    url = f"{base_url(data_root)}{path}"
    payload = None
    headers = {"Accept": "application/json"}
    if body is not None:
        payload = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=payload, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=API_TIMEOUT) as resp:
            raw = resp.read()
    except (urllib.error.URLError, OSError) as exc:
        print(f"[连接失败] 先运行 run/start.ps1 启动宿主（{exc}）", file=sys.stderr)
        raise SystemExit(1) from exc
    return json.loads(raw.decode("utf-8")) if raw else {}


def _require(name: str | None, action: str) -> str:
    if not name:
        print(f"用法: service_cli.py {action} <名称>", file=sys.stderr)
        raise SystemExit(2)
    return name


def cmd_ls(app: Path, data_root: Path) -> int:
    rows = _api(data_root, "GET", "/api/services").get("services") or []
    for s in rows:
        port = f":{s['port']}" if s.get("port") else ""
        uptime = f"  uptime={s['uptime_sec']}s" if s.get("uptime_sec") else ""
        auto = "  autostart" if s.get("autostart") else ""
        err = f"  ! {s['error']}" if s.get("error") else ""
        print(f"{s.get('status', ''):<10}{s.get('display', ''):<22}"
              f"{s.get('name', '')}{port}{uptime}{auto}{err}")
    return 0


def _svc_action(app: Path, data_root: Path, name: str | None, verb: str,
                action: str, via_cli: bool) -> int:
    name = _require(name, action)
    path = f"/api/services/{urllib.parse.quote(name)}/{verb}"
    if via_cli:
        path += "?via=cli"
    r = _api(data_root, "POST", path)
    print(f"{name} -> {r.get('status')} ({r.get('message')})")
    return 0 if r.get("ok", True) else 1


def cmd_svc_start(app: Path, data_root: Path, name: str | None) -> int:
    return _svc_action(app, data_root, name, "start", "svc-start", True)


def cmd_svc_stop(app: Path, data_root: Path, name: str | None) -> int:
    return _svc_action(app, data_root, name, "stop", "svc-stop", False)


def cmd_svc_restart(app: Path, data_root: Path, name: str | None) -> int:
    return _svc_action(app, data_root, name, "restart", "svc-restart", False)


def _fmt_last(c: dict) -> str:
    if c.get("last_run") is None:
        return "未运行过"
    if c.get("last_ok"):
        return f"ok ({c.get('last_dur_ms')}ms)"
    if c.get("last_rc") is None:
        return "FAIL (超时/未启动)"
    return f"FAIL (rc={c.get('last_rc')})"


def cmd_crons(app: Path, data_root: Path) -> int:
    rows = _api(data_root, "GET", "/api/crons").get("crons") or []
    for c in rows:
        state = ("invalid" if not c.get("valid") else
                 "running" if c.get("running") else
                 "idle" if c.get("enabled") else "disabled")
        err = f"  ! {c['error']}" if c.get("error") else ""
        print(f"{state:<9}{c.get('display', ''):<22}{c.get('name', '')}  "
              f"{c.get('schedule', '')}  {_fmt_last(c)}{err}")
    return 0


def cmd_cron_run(app: Path, data_root: Path, name: str | None) -> int:
    name = _require(name, "cron-run")
    r = _api(data_root, "POST", f"/api/crons/{urllib.parse.quote(name)}/run?via=cli")
    print(f"{name} -> {r.get('message')}")
    return 0 if r.get("ok", True) else 1


def cmd_events(app: Path, data_root: Path, name: str | None = None,
               count: int = 50) -> int:
    if name and name.isdigit():          # events 20 → 20 条，不过滤
        count, name = int(name), None
    q = f"/api/audit?n={count}"
    if name and name != "all":
        q += "&svc=" + urllib.parse.quote(name)
    for e in _api(data_root, "GET", q).get("events") or []:
        ok = "ok" if e.get("ok") else "FAIL"
        print(f"{e.get('ts', '')}  {e.get('svc') or '(host)':<20}"
              f"{e.get('act', ''):<18}{ok}  {e.get('note', '')}")
    return 0


def cmd_host_err(app: Path, data_root: Path, name: str | None = None,
                 count: int = 50) -> int:
    if name and name.isdigit():          # host-err 30 → 30 行
        count = int(name)
    for line in _api(data_root, "GET", f"/api/host-err?n={count}").get("lines") or []:
        print(line)
    return 0


def _reg_services_dir(data_root: Path) -> Path:
    """从 config.json 的 advanced.reg_dir 推导注册目录，缺省 reg。"""
    reg_dir = "reg"
    cfg_path = Path(data_root) / "config.json"
    if cfg_path.is_file():
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8-sig"))
            adv = cfg.get("advanced") if isinstance(cfg.get("advanced"), dict) else {}
            reg_dir = str(adv.get("reg_dir") or "reg")
        except Exception:  # noqa: BLE001 - 配置坏了也不该拖垮体检
            pass
    return Path(data_root) / reg_dir / "services"


def cmd_check(app: Path, data_root: Path, name: str | None = None,
              as_json: bool = False) -> int:
    """只读体检注册条：errors / derived / defaults；不启停、不改文件。

    主要给 AI/CI 用：全部有效退出码 0，存在 invalid 退出码 1。
    """
    from core.scanner import Scanner

    specs = Scanner(_reg_services_dir(data_root)).scan()
    if name:
        specs = {k: v for k, v in specs.items() if k == name}
    items: list[dict] = []
    for key in sorted(specs):
        spec = specs[key]
        errors = ([{"message": part} for part in spec.error.split("；") if part]
                  if spec.error else [])
        items.append({
            "name": key,
            "path": str(spec.path),
            "valid": spec.valid,
            "errors": errors,
            "derived": spec.derived,
            "defaults": spec.defaults,
        })
    invalid = sum(1 for item in items if not item["valid"])
    summary = {"total": len(items), "valid": len(items) - invalid,
               "invalid": invalid}
    if as_json:
        print(json.dumps({"ok": invalid == 0, "summary": summary, "items": items},
                         ensure_ascii=False, indent=2))
        return 0 if invalid == 0 else 1
    for item in items:
        flag = "OK " if item["valid"] else "ERR"
        print(f"{flag} {item['name']:<20} {item['path']}")
        for err in item["errors"]:
            print(f"    ! {err['message']}")
        for der in item["derived"]:
            print(f"    ~ {der['field']} = {der['value']}（{der['reason']}）")
        for dft in item["defaults"]:
            print(f"    · {dft['field']} 未写，默认 {dft['value']}")
    print(f"\n合计 {summary['total']} 条：有效 {summary['valid']}，"
          f"无效 {summary['invalid']}")
    return 0 if invalid == 0 else 1


HANDLERS = {
    # 宿主生命周期
    "start": cmd_start,
    "stop": cmd_stop,
    "restart": cmd_restart,
    "status": cmd_status,
    "shortcut": cmd_shortcut,
    "update-apply": cmd_update_apply,
    "apply-update-and-start": cmd_apply_update_and_start,
    # 服务管理（走 HTTP API）
    "ls": cmd_ls,
    "svc-start": cmd_svc_start,
    "svc-stop": cmd_svc_stop,
    "svc-restart": cmd_svc_restart,
    "crons": cmd_crons,
    "cron-run": cmd_cron_run,
    "events": cmd_events,
    "host-err": cmd_host_err,
    # 注册条体检（只读）
    "check": cmd_check,
}

NEEDS_NAME = {"svc-start", "svc-stop", "svc-restart", "cron-run"}
NEEDS_ARGS = {"events", "host-err"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="my-service",
        description="my-service 启停与服务管理（唯一实现）")
    parser.add_argument("action", nargs="?", default="start", choices=sorted(HANDLERS))
    parser.add_argument("name", nargs="?", default=None,
                        help="服务/任务名；events、host-err 也可传条数")
    parser.add_argument("count", nargs="?", type=int, default=50)
    parser.add_argument("--json", action="store_true", dest="as_json",
                        help="check 的输出改为 JSON（供 AI/CI 解析）")
    args = parser.parse_args(argv)
    app = APP_DIR
    data_root = resolve_data_root(app)
    if args.action == "check":
        return cmd_check(app, data_root, args.name, args.as_json)
    if args.action in NEEDS_NAME:
        return HANDLERS[args.action](app, data_root, args.name)
    if args.action in NEEDS_ARGS:
        return HANDLERS[args.action](app, data_root, args.name, args.count)
    return HANDLERS[args.action](app, data_root)


if __name__ == "__main__":
    raise SystemExit(main())
