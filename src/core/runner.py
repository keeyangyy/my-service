# -*- coding: utf-8 -*-
"""my-service · core/runner.py

纯工具层：构建命令行、启动托管进程、按停止模式终结进程、执行健康检查。
不做状态决策（那是 state.Manager 的事），只提供原子操作。

日志边界：本层不写任何日志文件——被管进程 stdout/stderr 一律 DEVNULL，服务日志由
服务自管（服务目录下的 logs）；框架侧的启停事件走 core/audit.py 的登记簿。
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import time
import urllib.request
from urllib.parse import urlsplit
from pathlib import Path

from .scanner import Spec

# 新进程组 + 隐藏窗口：taskkill /T 可整树收割；黑窗不打扰用户桌面
CREATE_FLAGS = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW

_STOP_WAIT_SEC = 10.0     # 温和等待进程自退的最长秒数
_STOP_SCRIPT_WAIT = 15.0  # stop_script 同步执行的兜底超时

# 健康探测强制禁用代理：localhost 服务不该走系统代理（clash 等）。
# 否则宿主进程若残留 http_proxy 环境变量，urllib 默认读代理会对本机端口
# 探测失败（URLError），导致 some-service 等被误判 stopped/failed。
_NO_PROXY_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

# 健康探测的 User-Agent——供服务侧识别"机器流量"并对其静默访问日志
# （规范见 C:\path\to\README.md「日志合规」：/health 不该灌进业务日志）
HEALTH_UA = "my-service-health"


def _interpreter_for(entry: Path) -> list[str]:
    ext = entry.suffix.lower()
    if ext == ".py":
        return [Path(__import__("sys").executable).as_posix()]
    if ext == ".ps1":
        return ["pwsh.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File"]
    if ext in (".cmd", ".bat"):
        return ["cmd.exe", "/c"]
    if ext == ".exe":
        return []
    raise ValueError(f"不支持的入口类型: {ext}（支持 .py/.ps1/.cmd/.bat/.exe）")


def build_command(spec: Spec) -> list[str]:
    if spec.mode not in ("managed", "detached", "on_demand"):
        raise ValueError(f"mode={spec.mode} 未支持")
    cmd = _interpreter_for(spec.entry_abs)
    cmd += [spec.entry_abs.as_posix()]
    cmd += list(spec.args)
    return cmd


class ProcessHandle:
    """一次成功 start 对应的运行句柄。

    框架不持有日志句柄——服务的输出不属于框架职责（见 README「日志边界」）。
    """

    def __init__(self, popen: subprocess.Popen, started_at: float,
                 pidfile: Path):
        self.popen = popen
        self.started_at = started_at
        self.pidfile = pidfile        # my-service\logs\pids\<svc>.pid（框架产物）

    @property
    def pid(self) -> int | None:
        return self.popen.pid


# ---------------------------------------------------------------- 启动/停止
_RUNTIME_DIR: Path | None = None    # my-service\logs —— 框架自家运行时产物


def set_runtime_dir(p: Path) -> Path:
    """宿主启动时注入运行时目录；被管服务的 pid 文件归置在其下 pids\\。"""
    global _RUNTIME_DIR
    _RUNTIME_DIR = Path(p)
    (_RUNTIME_DIR / "pids").mkdir(parents=True, exist_ok=True)
    return _RUNTIME_DIR


def pidfile_path(spec: Spec) -> Path:
    """pid 是框架自己需要的记账，故写框架家，不在服务目录里留痕。"""
    base = _RUNTIME_DIR or Path(__file__).resolve().parents[2] / "logs"
    return base / "pids" / f"{spec.folder}.pid"


def start_process(spec: Spec, log_base: Path | None = None) -> ProcessHandle:
    """拉起进程：stdout/stderr 一律丢弃（DEVNULL），pid 写 my-service\\logs\\pids\\。

    服务日志由服务自己写（它家 logs\\），框架零接管——纳管与否不改变服务的日志形态。
    代价：服务自己 print 的启动报错不留档，故失败原因由审计流 note 字段兜住。
    log_base 参数保留兼容旧签名（已弃用）。
    """
    cmd = build_command(spec)
    env = {**os.environ, **spec.env}
    proc = subprocess.Popen(
        cmd,
        cwd=str(spec.workdir),
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=CREATE_FLAGS,
    )
    try:
        pidfile_path(spec).write_text(str(proc.pid), encoding="ascii")
    except OSError:
        pass
    return ProcessHandle(proc, time.time(), pidfile_path(spec))


def _taskkill_tree(pid: int) -> None:
    # taskkill /T 收割整棵进程树（覆盖 cmd.exe→孙子进程），/F 强制
    subprocess.run(
        ["taskkill", "/PID", str(pid), "/T", "/F"],
        capture_output=True, timeout=8, check=False,
    )


def pid_alive(pid: int) -> bool:
    """Windows 下判断指定 PID 的进程是否存在（tasklist 精确匹配）。"""
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True, text=True, timeout=6, check=False,
        )
    except Exception:  # noqa: BLE001
        return False
    return out.returncode == 0 and str(pid) in (out.stdout or "")


def wait_gone(pid: int, deadline: float) -> bool:
    """轮询进程是否消失（managed 进程对象不在手边时的通用判据）。"""
    while time.time() < deadline:
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True, text=True, timeout=6, check=False,
        )
        if out.returncode != 0 or f"{pid}" not in (out.stdout or ""):
            return True
        time.sleep(0.3)
    return False


def _drop_pidfile(handle: ProcessHandle) -> None:
    try:
        handle.pidfile.unlink(missing_ok=True)
    except OSError:
        pass


def close_handle(handle: ProcessHandle) -> None:
    """回收框架侧产物（pid 文件）。进程已退出时用，无日志句柄可关。"""
    _drop_pidfile(handle)


def stop_managed(handle: ProcessHandle) -> None:
    """温和等待进程自退 → taskkill /T /F 整树收割 → 清理 pid 文件。"""
    proc, pid = handle.popen, handle.popen.pid
    try:
        time.sleep(2.0)  # 给 python HTTP 服务一点点体面退出的机会
        if proc.poll() is None:
            _taskkill_tree(pid)
            if not wait_gone(pid, time.time() + _STOP_WAIT_SEC):
                pass  # 树杀基本必中；此处留兜底不阻塞
    finally:
        _drop_pidfile(handle)


def run_stop_script(spec: Spec) -> None:
    script = spec.stop_script_abs
    if not script or not script.is_file():
        raise FileNotFoundError(f"stop_script 不存在: {script}")
    cmd = _interpreter_for(script) + [script.as_posix()]
    subprocess.run(
        cmd,
        cwd=str(spec.workdir), capture_output=True,
        timeout=_STOP_SCRIPT_WAIT, check=False,
        creationflags=CREATE_FLAGS,
    )


# ---------------------------------------------------------------- 健康检查
def parse_http_host_port(url: str) -> tuple[str, int | None]:
    """从 http 健康 URL 解析 (host, port)。解析失败返回 (127.0.0.1, None)。"""
    try:
        p = urlsplit(url or "")
        host = p.hostname or "127.0.0.1"
        port = p.port or (443 if p.scheme == "https" else 80)
        return host, port
    except Exception:  # noqa: BLE001
        return "127.0.0.1", None


def port_listening(host: str, port: int, timeout: float = 0.05) -> bool:
    """快速端口可达判定——只测端口是否被监听，不做完整 HTTP 探测。

    用极短超时（50ms）：本机 loopback 上真服务握手只需亚毫秒~十几毫秒，
    而无监听的端口本应瞬时拒绝——个别环境（clash TUN/驱动劫持）会把"拒绝"
    拖成数秒级超时，宿主绝不能傻等。50ms 足够真服务应答，又能立刻放弃空门。
    """
    if not port:
        return False
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def quick_alive(spec: Spec, handle: ProcessHandle | None) -> bool:
    """快速「实例是否已在运行」判定（防双开 / starting 阶段用）。

    只做廉价的端口/进程探测，不做 urlopen（urlopen 在本机无监听时可能挂数秒）。
    process 型要求 handle 在场且进程存活；none 型保守返回 True（不做判定）。
    """
    if spec.health_type == "port" and spec.health_port:
        return port_listening("127.0.0.1", spec.health_port)
    if spec.health_type == "http":
        host, port = parse_http_host_port(spec.health_url or "")
        return port_listening(host, port) if port else True
    if spec.health_type == "process":
        return handle is not None and handle.popen.poll() is None
    return True


def check_health(spec: Spec, handle: ProcessHandle | None) -> tuple[bool, str]:
    """返回 (通过?, 说明)。process 型要求 handle 在场。"""
    alive, why, _last = check_health_ext(spec, handle)
    return alive, why


def check_health_ext(spec: Spec, handle: ProcessHandle | None) -> tuple[bool, str, float | None]:
    """返回 (通过?, 说明, last_active?)。

    http 型健康响应体是 JSON 且带数字字段 last_active 时解析出来——
    供 on_demand 模式空闲回收用（服务自报"最近一次业务请求"时间戳；
    框架自身的探测不算活动，协议见 README）。解析失败/无字段不影响健康结论。
    """
    htype = spec.health_type
    try:
        if htype == "none":
            return True, "", None
        if htype == "process":
            if handle is None or handle.popen.poll() is not None:
                return False, "进程不存在", None
            return True, "", None
        if htype == "port":
            assert spec.health_port is not None
            with socket.create_connection(("127.0.0.1", spec.health_port), timeout=1.5):
                return True, "", None
        if htype == "http":
            assert spec.health_url
            req = urllib.request.Request(spec.health_url,
                                        headers={"User-Agent": HEALTH_UA})
            with _NO_PROXY_OPENER.open(req, timeout=2.5) as resp:
                last_active: float | None = None
                try:
                    payload = json.loads(resp.read().decode("utf-8", errors="ignore"))
                    if isinstance(payload, dict) and isinstance(payload.get("last_active"), (int, float)):
                        last_active = float(payload["last_active"])
                except Exception:  # noqa: BLE001 - 响应非 JSON/空体，不影响健康结论
                    pass
                if getattr(resp, "status", 200) < 400:
                    return True, "", last_active
                return False, f"HTTP {resp.status}", last_active
    except OSError as exc:
        return False, f"健康检查未通过: {exc.__class__.__name__}: {exc}", None
    except Exception as exc:  # noqa: BLE001
        return False, f"健康检查异常: {exc}", None
    return False, f"未知健康类型 {htype}", None
