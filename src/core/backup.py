# -*- coding: utf-8 -*-
"""my-service · core/backup.py

实例配置备份：把 `reg/`、`config.json`、`adapters/`（若存在）复制到 `bak/<时间戳>/`。

只做复制，不修改、不删除源文件。备份目录不进 git（见 .gitignore）。
"""
from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

BACKUP_DIRNAME = "bak"


def _copy_tree(src: Path, dest: Path) -> int:
    shutil.copytree(src, dest)
    return sum(1 for f in dest.rglob("*") if f.is_file())


def create_backup(base: Path) -> dict:
    """创建一份备份 → {name, path, files}。同秒重复调用会带序号后缀。"""
    base = Path(base)
    root = base / BACKUP_DIRNAME
    root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = root / stamp
    n = 1
    while dest.exists():
        dest = root / f"{stamp}-{n}"
        n += 1
    dest.mkdir(parents=True)

    files = 0
    reg = base / "reg"
    if reg.is_dir():
        files += _copy_tree(reg, dest / "reg")
    cfg = base / "config.json"
    if cfg.is_file():
        shutil.copy2(cfg, dest / "config.json")
        files += 1
    adapters = base / "adapters"
    if adapters.is_dir():
        files += _copy_tree(adapters, dest / "adapters")

    return {"name": dest.name, "path": str(dest), "files": files}


def list_backups(base: Path, limit: int = 20) -> list[dict]:
    """按时间倒序列出备份 → [{name, path, size}]。"""
    root = Path(base) / BACKUP_DIRNAME
    if not root.is_dir():
        return []
    items = sorted((p for p in root.iterdir() if p.is_dir()),
                   key=lambda p: p.name, reverse=True)[:limit]
    out: list[dict] = []
    for d in items:
        size = sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
        out.append({"name": d.name, "path": str(d), "size": size})
    return out
