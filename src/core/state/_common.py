# -*- coding: utf-8 -*-
"""my-service · core/state/_common.py

状态机公共常量与小工具（manager 与 monitor mixin 共用）。
"""
from __future__ import annotations

import time

TICK_SEC = 1.0
TICK_FAST_SEC = 1.0      # 面板活跃时的收敛间隔
TICK_IDLE_SEC = 10.0     # 面板不活跃时的收敛间隔
PANEL_GRACE_SEC = 15.0   # 多久没有面板心跳算“不活跃”


def _dur(t0: float) -> int:
    """距 t0 的毫秒数（登记簿 dur_ms 用）。"""
    return int((time.time() - t0) * 1000)