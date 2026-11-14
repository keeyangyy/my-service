"""my-service · core/state —— 服务状态机。

对外只暴露 Manager（兼容 `from core.state import Manager`）。
职责拆分见 manager.py 顶部说明。
"""
from ._common import TICK_SEC
from .manager import Manager

__all__ = ["Manager", "TICK_SEC"]
