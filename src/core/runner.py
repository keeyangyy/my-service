# -*- coding: utf-8 -*-
"""my-service · core/runner.py

纯工具层：构建命令行、启动托管进程、按停止模式终结进程、执行健康检查。
不做状态决策（那是 state.Manager 的事），只提供原子操作。
"""
from __future__ import annotations

import os
import socket
import subprocess
import time
import urllib.request
from pathlib import Path

from .scanner import Spec

# 新进程组 + 隐藏窗口：taskkill /T 可整树收割；黑窗不打扰用户桌面
CREATE_FLAGS = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW

_STOP_WAIT_SEC = 10.0     # 温和等待进程自退的最长秒数
_STOP_SCRIPT_WAIT = 15.0  # stop_script 同步执行的兜底超时


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
    if spec.mode not in ("managed", "detached"):
        raise ValueError(f"mode={spec.mode} 未支持")
    cmd = _interpreter_for(spec.entry_abs)
    cmd += [spec.entry_abs.as_posix()]
    cmd += list(spec.args)
    return cmd


class ProcessHandle:
    """一次成功 start 对应的运行句柄。"""

    def __init__(self, popen: subprocess.Popen, log_fhs, started_at: float,
                 logs_dir: Path):
        self.popen = popen
        self.log_fhs = log_fhs        # (stdout_fh, stderr_fh) 追加句柄，stop 后关闭
        self.started_at = started_at
        self.rdir = logs_dir          # 该服务的 logs\ 目录（pid/日志所在）

    @property
    def pid(self) -> int | None:
        return self.popen.pid


# ---------------------------------------------------------------- 启动/停止
def logs_dir(spec: Spec) -> Path:
    """该服务的运行时产物目录（两档模型约定）：logs\\service.log / service.err.log / service.pid"""
    return spec.path / "logs"


def _tail_path(p: Path):
    return open(p, "ab", buffering=0)


def start_process(spec: Spec, log_base: Path | None = None) -> ProcessHandle:
    """拉起进程：stdout/stderr 分流写入服务自己的 logs\\service.log[.err]，并写 pid 文件。

    log_base 参数保留兼容旧签名（已弃用）。
    """
    cmd = build_command(spec)
    rdir = logs_dir(spec)
    rdir.mkdir(parents=True, exist_ok=True)
    out_fh = _tail_path(rdir / "service.log")
    err_fh = _tail_path(rdir / "service.err.log")
    env = {**os.environ, **spec.env}
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(spec.workdir),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=out_fh,
            stderr=err_fh,
            creationflags=CREATE_FLAGS,
        )
    except Exception:
        out_fh.close()
        err_fh.close()
        raise
    stamp = time.strftime('%Y-%m-%d %H:%M:%S')
    try:
        out_fh.write(f"\n===== my-service 启动 {stamp} pid={proc.pid} =====\n".encode("utf-8"))
        err_fh.write(f"\n===== my-service 启动 {stamp} pid={proc.pid} =====\n".encode("utf-8"))
        (rdir / "service.pid").write_text(str(proc.pid), encoding="ascii")
    except OSError:
        pass
    return ProcessHandle(proc, (out_fh, err_fh), time.time(), rdir)


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


def close_handle(handle: ProcessHandle) -> None:
    """仅回收文件句柄（进程已退出时用）。"""
    for fh in handle.log_fhs:
        try:
            fh.close()
        except OSError:
            pass
    try:
        (handle.rdir / "service.pid").unlink(missing_ok=True)
    except OSError:
        pass


def stop_managed(handle: ProcessHandle) -> None:
    """温和等待 2 秒 → taskkill /T /F 树杀 → 清理 pid 文件与日志句柄。"""
    proc, pid = handle.popen, handle.popen.pid
    try:
        time.sleep(2.0)  # 给 python HTTP 服务一点点体面退出的机会
        if proc.poll() is None:
            _taskkill_tree(pid)
            if not wait_gone(pid, time.time() + _STOP_WAIT_SEC):
                pass  # 树杀基本必中；此处留兜底不阻塞
    finally:
        for fh in handle.log_fhs:
            try:
                fh.close()
            except OSError:
                pass
        try:
            (handle.rdir / "service.pid").unlink(missing_ok=True)
        except OSError:
            pass


def run_stop_script(spec: Spec) -> None:
    script = spec.stop_script_abs
    if not script or not script.is_file():
        raise FileNotFoundError(f"stop_script 不存在: {script}")
    cmd = _interpreter_for(script) + [script.as_posix()]
    subprocess.run(
        cmd,
        cwd=str(spec.workdir), capture_output=True,
        timeout=_STOP_SCRIPT_WAIT, check=False,
    )


# ---------------------------------------------------------------- 健康检查
def check_health(spec: Spec, handle: ProcessHandle | None) -> tuple[bool, str]:
    """返回 (通过?, 说明)。process 型要求 handle 在场。"""
    htype = spec.health_type
    try:
        if htype == "none":
            return True, ""
        if htype == "process":
            if handle is None or handle.popen.poll() is not None:
                return False, "进程不存在"
            return True, ""
        if htype == "port":
            assert spec.health_port is not None
            with socket.create_connection(("127.0.0.1", spec.health_port), timeout=1.5):
                return True, ""
        if htype == "http":
            assert spec.health_url
            with urllib.request.urlopen(spec.health_url, timeout=2.5) as resp:
                if getattr(resp, "status", 200) < 400:
                    return True, ""
                return False, f"HTTP {resp.status}"
    except OSError as exc:
        return False, f"健康检查未通过: {exc.__class__.__name__}"
    except Exception as exc:  # noqa: BLE001
        return False, f"健康检查异常: {exc}"
    return False, f"未知健康类型 {htype}"
