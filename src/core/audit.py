# -*- coding: utf-8 -*-
"""my-service · core/audit.py — 控制面登记簿（生命周期审计流）

只记 my-service 自己对服务的编排动作：谁、何时、做了什么、成没成、一句话原因。
一行一条 JSONL，不掺任何服务的业务输出。

单一作者原则（一个文件只有一个写的人）：
    my-service\\logs\\host.jsonl    本模块独写 —— 启停登记簿
    my-service\\logs\\host.err.log  宿主自身异常栈（server.py 接 sys.stderr）
    <服务 path>\\logs\\*             服务亲笔 —— 框架不写、不改、不接管其输出

记录字段（固定 7 个，缺省即不写该键）：
    ts       "2026-09-05 16:15:36.412"（本地时区，毫秒）
    svc      服务名；宿主自身事件为 null
    act      动作，取值见 ACTS
    ok       成/败
    dur_ms   耗时（毫秒），可选
    pid      相关进程 pid，可选
    note     一句话原因（截断 300 字），可选
"""
from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path

MAX_BYTES = 5 * 1024 * 1024   # 登记簿按大小轮转（框架自家文件，几行成本）
BACKUPS = 3
NOTE_MAX = 300

# 动作枚举：新增动作必须同步 web/app.js 的 ACT_MAP 与 README 事件字典
ACTS = (
    "host_start", "host_stop",          # 宿主自身
    "start", "stop",                        # 用户/CLI/API 发起（restart 表现为 stop→start 两条）
    "autostart", "autostart_skip",      # 开机自启拉起 / 已在运行而跳过
    "adopt_external",                   # 外部实例归位
    "health_flip",                      # 运行中健康翻转（转坏/恢复）
    "idle_reclaim",                     # on_demand 空闲回收
    "crash",                            # 托管进程意外退出
    "spawn_fail",                       # 进程根本没能起来（入口不存在/拒绝访问）
    "reject_double",                    # 防双开拒绝启动
    "spec_invalid",                     # 注册条被判无效/被移除
    "cron_run",                         # 定时任务执行一轮（成功/失败/超时/启动失败）
    "cron_skip",                        # 定时任务错过计划时刻且不补跑，顺延
)

_FILE = "host.jsonl"
_lock = threading.Lock()
_path: Path | None = None


def init(logs_dir: Path) -> Path:
    """定位登记簿文件（幂等）。由 server.main 在起 Manager 之前调用。"""
    global _path
    _path = Path(logs_dir) / _FILE
    _path.parent.mkdir(parents=True, exist_ok=True)
    return _path


def now_ts() -> str:
    dt = datetime.now()
    return dt.strftime("%Y-%m-%d %H:%M:%S.") + f"{dt.microsecond // 1000:03d}"


def emit(act: str, ok: bool = True, svc: str | None = None,
         dur_ms: int | None = None, pid: int | None = None,
         note: str = "") -> dict:
    """追加一条事件。任何写盘失败都被吞掉——登记簿不得拖垮编排主流程。"""
    if act not in ACTS:
        note = (note + " " if note else "") + f"[未知动作 {act}]"
    rec: dict = {"ts": now_ts(), "svc": svc, "act": act, "ok": bool(ok)}
    if dur_ms is not None:
        rec["dur_ms"] = int(dur_ms)
    if pid:
        rec["pid"] = int(pid)
    if note:
        rec["note"] = str(note)[:NOTE_MAX]
    if _path is None:      # 未 init（单测/脚本直调）→ 只构造不落盘
        return rec
    line = json.dumps(rec, ensure_ascii=False, separators=(",", ":"))
    try:
        with _lock:
            _rotate_if_needed()
            with open(_path, "a", encoding="utf-8", newline="\n") as fh:
                fh.write(line + "\n")
    except OSError:
        pass
    return rec


def _rotate_if_needed() -> None:
    try:
        if not _path.is_file() or _path.stat().st_size < MAX_BYTES:
            return
    except OSError:
        return
    base = str(_path)
    for i in range(BACKUPS - 1, 0, -1):
        src = Path(f"{base}.{i}")
        if src.is_file():
            try:
                src.replace(Path(f"{base}.{i + 1}"))
            except OSError:
                pass
    try:
        _path.replace(Path(f"{base}.1"))
    except OSError:
        pass


def current_path() -> Path | None:
    """当前登记簿文件位置（未 init 时为 None），供接口层如实回报来源。"""
    return _path


def tail(n: int = 200, svc: str | None = None,
         only_fail: bool = False) -> list[dict]:
    """读登记簿尾部；坏行（轮转截断/半行）跳过不报错。"""
    if _path is None or not _path.is_file():
        return []
    try:
        size = _path.stat().st_size
        with open(_path, "rb") as fh:
            if size > 512 * 1024:
                fh.seek(-512 * 1024, 2)
            raw = fh.read()
    except OSError:
        return []
    out: list[dict] = []
    for line in raw.decode("utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if not isinstance(rec, dict):
            continue
        if svc and rec.get("svc") != svc:
            continue
        if only_fail and rec.get("ok") is not False:
            continue
        out.append(rec)
    return out[-n:]
