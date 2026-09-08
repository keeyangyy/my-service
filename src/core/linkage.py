# -*- coding: utf-8 -*-
"""my-service · core/linkage.py — 服务与定时任务的归属关系

判定依据：**文件系统路径**，不是命名约定。

cron 注册条的 `path`（脚本所在家）落在哪个已注册服务的 `path` 之下（或与其相等），
该任务就归属那个服务。

为什么用路径而不是名字前缀：
- 路径是物理事实（脚本真的躺在服务自己的目录里），名字前缀只是巧合
- 改名不会破坏关联，反之亦然

Why: 面板要把「关联的定时任务」显示在服务的设置面板里，让"这个服务每天在跑什么"
一眼可见；不需要用户额外维护任何字段。

How to apply: 由 state.Manager 在 snapshot 时调用 link_crons()，为每个服务附加
cron_names 字段；匹配不到任何服务的任务即"无关联"，继续在定时任务区独立显示。
"""
from __future__ import annotations

from pathlib import PureWindowsPath


def _norm(p) -> str:
    """路径归一：统一正斜杠、去末尾斜杠、转小写。

    Windows 文件系统大小写不敏感，故统一小写比较；用 PureWindowsPath
    保证在任何平台上都能按 Windows 语义处理盘符与分隔符。
    """
    s = str(p).replace("/", "\\").rstrip("\\")
    return str(PureWindowsPath(s)).lower()


def is_under(child, parent) -> bool:
    """child 是否位于 parent 之下（含相等）。

    Windows 大小写不敏感；`D:\\a\\bc` 不算在 `D:\\a\\b` 之下（按分量比较，
    不是字符串前缀），避免同名前缀误判。
    """
    c, p = _norm(child), _norm(parent)
    if c == p:
        return True
    # 末尾补分隔符再比前缀，确保按完整分量匹配
    return c.startswith(p.rstrip("\\") + "\\")


def link_crons(specs: dict, cron_specs: dict) -> dict[str, list[str]]:
    """把每个 cron 任务归到一个服务名下。

    Args:
        specs: {服务名: Spec}  —— 需含 .path
        cron_specs: {任务名: CronSpec} —— 需含 .path

    Returns:
        {服务名: [任务名, ...]}，按任务名字母序；无关联的服务不出现在结果里
        （调用方取 .get(name, []) 即可）。

    一个任务只归属**路径最长**的那个服务（嵌套场景取最具体者）。
    """
    services = [(name, getattr(s, "path", None)) for name, s in specs.items()]
    services = [(n, p) for n, p in services if p is not None]

    result: dict[str, list[str]] = {}
    for cname, cspec in cron_specs.items():
        cpath = getattr(cspec, "path", None)
        if cpath is None:
            continue
        best_name, best_len = None, -1
        for sname, spath in services:
            if not is_under(cpath, spath):
                continue
            # 取最长匹配，处理服务目录互相嵌套的场景
            cur = len(_norm(spath))
            if cur > best_len:
                best_name, best_len = sname, cur
        if best_name is not None:
            result.setdefault(best_name, []).append(cname)

    for names in result.values():
        names.sort()
    return result
