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
    cmd = _interpreter_for(spec.entry_abs)
    if spec.mode != "managed":
        raise ValueError(f"mode={spec.mode} 当前版本仅支持 managed")
    cmd += [spec.entry_abs.as_posix()]
    cmd += list(spec.args)
    return cmd


class ProcessHandle:
    """一次成功 start 对应的运行句柄。"""

    def __init__(self, popen: subprocess.Popen, log_fh, started_at: float):
        self.popen = popen
        self.log_fh = log_fh          # append 模式的日志文件句柄，stop 后关闭
        self.started_at = started_at

    @property
    def pid(self) -> int | None:
        return self.popen.pid


def start_process(spec: Spec, log_path: Path) -> ProcessHandle:
    """拉起进程并把 stdout/stderr 重定向到日志文件。抛异常由上层转为 failed。"""
    cmd = build_command(spec)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(log_path, "ab", buffering=0)  # 二进制追加直写，fmt 由子进程自己决定
    env = {**os.environ, **spec.env}
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(spec.workdir),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=fh,
            stderr=subprocess.STDOUT,
            creationflags=CREATE_FLAGS,
        )
    except Exception:
        fh.close()
        raise
    banner = f"\n===== my-service 启动 {time.strftime('%Y-%m-%d %H:%M:%S')} pid={proc.pid} =====\n".encode("utf-8")
    try:
        fh.write(banner)
    except OSError:
        pass
    return ProcessHandle(proc, fh, time.time())


def _taskkill_tree(pid: int) -> None:
    # taskkill /T 收割整棵进程树（覆盖 cmd.exe→孙子进程），/F 强制
    subprocess.run(
        ["taskkill", "/PID", str(pid), "/T", "/F"],
        capture_output=True, timeout=8, check=False,
    )


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


def stop_managed(handle: ProcessHandle) -> None:
    """温和等待 2 秒 → taskkill /T /F 树杀 → 轮询确认消失 → 关日志句柄。"""
    proc, pid = handle.popen, handle.popen.pid
    try:
        time.sleep(2.0)  # 给 python HTTP 服务一点点体面退出的机会
        if proc.poll() is None:
            _taskkill_tree(pid)
            if not wait_gone(pid, time.time() + _STOP_WAIT_SEC):
                pass  # 树杀基本必中；此处留兜底不阻塞
        else:
            wait_gone(pid, time.time() + 2.0)
    finally:
        try:
            handle.log_fh.close()
        except OSError:
            pass


def run_stop_script(spec: Spec) -> None:
    script = spec.stop_script_abs
    if not script or not script.is_file():
        raise FileNotFoundError(f"stop_script 不存在: {script}")
    subprocess.run(
        ["cmd.exe", "/c", script.as_posix()],
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
