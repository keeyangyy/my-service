"""my-service · core/backup.py

实例配置备份：把 `reg/`、`config.json`、`adapters/`（若存在）复制到 `bak/<时间戳>/`。

不修改、不删除源文件。备份目录不进 git（见 .gitignore）。
保留策略：`bak/` 下只留最近 `keep` 份（默认 7，0 = 不自动清理），见 `prune_backups()`。
"""
from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

BACKUP_DIRNAME = "bak"
DEFAULT_KEEP = 7


def _copy_tree(src: Path, dest: Path) -> int:
    shutil.copytree(src, dest)
    return sum(1 for f in dest.rglob("*") if f.is_file())


def _backup_dirs(base: Path) -> list[Path]:
    """bak/ 下的备份目录，按名字（时间戳）倒序 = 新→旧。"""
    root = Path(base) / BACKUP_DIRNAME
    if not root.is_dir():
        return []
    return sorted((p for p in root.iterdir() if p.is_dir()),
                  key=lambda p: p.name, reverse=True)


def prune_backups(base: Path, keep: int = DEFAULT_KEEP) -> list[str]:
    """只保留最近 `keep` 份备份，删掉更旧的 → 被删除的目录名。

    keep <= 0 表示不自动清理（保留全部）。删除失败（文件占用）不中断，
    跳到下一份——下次备份时还会再尝试。
    """
    try:
        keep = int(keep)
    except (TypeError, ValueError):
        keep = DEFAULT_KEEP
    if keep <= 0:
        return []
    removed: list[str] = []
    for d in _backup_dirs(base)[keep:]:
        try:
            shutil.rmtree(d)
        except OSError:
            continue
        removed.append(d.name)
    return removed


def create_backup(base: Path, keep: int = DEFAULT_KEEP) -> dict:
    """创建一份备份 → {name, path, files, pruned}。同秒重复调用会带序号后缀。

    keep > 0 时顺手清理超出保留份数的旧备份。
    """
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

    return {"name": dest.name, "path": str(dest), "files": files,
            "pruned": prune_backups(base, keep)}


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
