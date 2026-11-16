"""my-service · core/update.py

便携包一键升级：检查 GitHub Release 的 `latest.json` → 下载 zip → 校验 sha256 →
把清单内的代码（`src/ web/ run/ docs/` + 三个文档）解压到 `data/update/code.new/`
→ 写待生效标记。

**真正的目录替换由 launcher（包根 `start.cmd`）在下次启动 python 之前完成**
（`src/service_cli.py update-apply`）：Windows 上运行中的代码目录被 cwd/文件句柄
占用，无法重命名；旧宿主退出、新宿主尚未起来的那一刻才是唯一安全窗口。
替换前旧代码整体挪到 `data/bak/code-prev/`，中途失败会回滚并保留标记，下次启动再试。
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import time
import urllib.request
import zipfile
from pathlib import Path

DEFAULT_REPO = "keeyangyy/my-service"
TIMEOUT = 20
UA = "my-service-updater"
UPDATE_SUBDIR = Path("data") / "update"
BACKUP_SUBDIR = Path("data") / "bak"
# 升级替换清单（与 tools/build-portable.ps1 的 CODE_DIRS / CODE_FILES 保持一致）：
# 包根 = 仓库结构，没有 app/ 层；只换这几个目录与文档，reg/ data/ config.json 一律不动。
CODE_DIRS = ("src", "web", "run", "docs")
CODE_FILES = ("README.md", "CHANGELOG.md", "LICENSE")
def is_source_worktree(home: Path) -> bool:
    """是否源码工作区（包根有 .git）。

    只用于给前端一句提示（升级后工作区会与 git 不一致），**不阻止升级**：
    任何部署形态都允许检查更新与下发替换清单。
    """
    return (Path(home) / ".git").exists()


def _ver_tuple(version: str) -> tuple[int, ...]:
    """把 "0.13.0" 变成 (0,13,0)，便于逐段比较（字符串比较会让 0.9 > 0.10）。"""
    parts: list[int] = []
    for chunk in version.strip().lstrip("vV").split("."):
        try:
            parts.append(int(chunk))
        except ValueError:
            parts.append(0)
    return tuple(parts)


def is_newer(latest: str, current: str) -> bool:
    return _ver_tuple(latest) > _ver_tuple(current)


def latest_url(repo: str = DEFAULT_REPO) -> str:
    return f"https://github.com/{repo}/releases/latest/download/latest.json"


def check(current: str, repo: str = DEFAULT_REPO, timeout: float = TIMEOUT) -> dict:
    """拉最新版本清单 → {ok, current, latest, has_update, url, sha256, size}。"""
    req = urllib.request.Request(latest_url(repo), headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("latest.json 根节点必须是对象")
    latest = str(data.get("version") or "")
    url = str(data.get("url") or "")
    if not latest or not url:
        raise ValueError("latest.json 缺少 version 或 url")
    return {
        "ok": True,
        "current": current,
        "latest": latest,
        "has_update": is_newer(latest, current),
        "url": url,
        "sha256": str(data.get("sha256") or ""),
        "size": int(data.get("size") or 0),
        "notes": str(data.get("notes") or ""),
    }


def _download(url: str, dest: Path, timeout: float = 120.0) -> str:
    """下载到 dest 并返回 sha256（流式，避免整包进内存）。"""
    digest = hashlib.sha256()
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp, dest.open("wb") as fh:
        while True:
            chunk = resp.read(256 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            fh.write(chunk)
    return digest.hexdigest()


def _code_entries(root: Path) -> list[Path]:
    """代码根下属于替换清单的条目（目录 + 文档）。"""
    entries = [root / name for name in CODE_DIRS if (root / name).is_dir()]
    entries += [root / name for name in CODE_FILES if (root / name).is_file()]
    return entries


def _pick_code_root(staging: Path) -> Path | None:
    """定位代码根。支持三种包布局：

    - 带版本目录：`my-service-x.y.z/src/server.py`（构建脚本当前产物）
    - 包根即代码根：`src/server.py` 直接在 zip 顶层（v0.17.1 及以前）
    - 旧布局：`my-service-x.y.z/app/src/server.py`
    """
    if (staging / "src" / "server.py").is_file():
        return staging
    for top in staging.iterdir():
        if not top.is_dir():
            continue
        if (top / "src" / "server.py").is_file():
            return top
        legacy = top / "app"
        if (legacy / "src" / "server.py").is_file():
            return legacy
    return None


def apply(home: Path, info: dict) -> dict:
    """下载 + 校验 + 按清单暂存到 data/update/code.new，并写 pending.json。

    返回 {ok, version, note} 或 {ok: False, error}。
    """
    version = str(info.get("version") or "")
    url = str(info.get("url") or "")
    sha256 = str(info.get("sha256") or "")
    if not version or not url:
        return {"ok": False, "error": "缺少 version 或 url"}

    upd = Path(home) / UPDATE_SUBDIR
    shutil.rmtree(upd / "staging", ignore_errors=True)
    shutil.rmtree(upd / "code.new", ignore_errors=True)
    upd.mkdir(parents=True, exist_ok=True)

    zip_path = upd / f"my-service-{version}.zip"
    actual = _download(url, zip_path)
    if sha256 and actual.lower() != sha256.lower():
        zip_path.unlink(missing_ok=True)
        return {"ok": False, "error": "sha256 校验失败，已丢弃下载包"}

    staging = upd / "staging"
    try:
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(staging)
    except (OSError, zipfile.BadZipFile) as exc:
        return {"ok": False, "error": f"解压失败: {exc}"}

    code_src = _pick_code_root(staging)
    if code_src is None:
        return {"ok": False, "error": "包内缺少 src/server.py"}
    entries = _code_entries(code_src)
    if not entries:
        return {"ok": False, "error": "包内没有可替换的代码目录"}

    target = upd / "code.new"
    target.mkdir(parents=True, exist_ok=True)
    for entry in entries:
        shutil.move(str(entry), str(target / entry.name))
    shutil.rmtree(staging, ignore_errors=True)
    zip_path.unlink(missing_ok=True)

    flag = upd / "pending.json"
    flag.write_text(json.dumps({"version": version}, ensure_ascii=False) + "\n",
                    encoding="utf-8")
    return {"ok": True, "version": version,
            "note": "重启后由 start.cmd 调 update-apply 替换代码目录并生效"}


def pending_version(home: Path) -> str | None:
    """读取待生效版本（无待生效返回 None）。"""
    flag = Path(home) / UPDATE_SUBDIR / "pending.json"
    if not flag.is_file():
        return None
    try:
        data = json.loads(flag.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    version = str(data.get("version") or "")
    return version or None


def apply_pending(home: Path) -> dict:
    """把 data/update/code.new 按清单替换到 home（launcher 起 python 前调用）。

    步骤：旧代码整体挪到 data/bak/code-prev/ → 新代码就位 → 删 pending.json。
    中途失败：新代码退回暂存区、旧代码搬回原位，保留 pending.json 供下次重试，
    并把失败详情写到 data/update/last-error.json。

    两个防护：
    - cwd：更新器曾以 src 为 cwd，Windows 下不能移动/删除进程当前目录，先切到 home；
    - 并发：宿主派生的更新器与手动 run\\start.ps1 可能同时进来，用 apply.lock 串行化。
    """
    home = Path(home)
    upd = home / UPDATE_SUBDIR
    if not (upd / "code.new").is_dir():
        return {"ok": False, "error": "没有待生效的升级（data/update/code.new 不存在）"}

    with contextlib.suppress(OSError):
        cwd = Path.cwd()
        src_dir = home / "src"
        if cwd == src_dir or src_dir in cwd.parents:
            os.chdir(home)

    lock = upd / "apply.lock"
    try:
        lock_fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return {"ok": False, "error": "另一个升级进程正在执行（data/update/apply.lock）"}
    try:
        return _apply_pending_locked(home, upd)
    finally:
        os.close(lock_fd)
        with contextlib.suppress(OSError):
            lock.unlink()


def _apply_pending_locked(home: Path, upd: Path) -> dict:
    """apply_pending 的实际替换（调用方已持有 apply.lock 并修正 cwd）。"""
    flag = upd / "pending.json"
    new = upd / "code.new"
    version = pending_version(home) or ""
    backup = home / BACKUP_SUBDIR / "code-prev"
    names = [n for n in CODE_DIRS + CODE_FILES if (new / n).exists()]
    if not names:
        return {"ok": False, "error": "暂存区为空（data/update/code.new 里没有清单项）"}
    # 替换以「目录」为单位：暂存的 src 若不完整，替换后整棵树会残废，宁可拒绝
    staged_src = new / "src"
    if staged_src.exists() and not ((staged_src / "server.py").is_file()
                                    and (staged_src / "core").is_dir()):
        return {"ok": False,
                "error": "暂存的 src 不完整（缺 server.py 或 core/），已放弃替换"}
    shutil.rmtree(backup, ignore_errors=True)
    backup.mkdir(parents=True, exist_ok=True)

    moved: list[str] = []       # 已挪进备份的旧条目
    done: list[str] = []        # 已就位的新条目
    try:
        for name in names:
            current = home / name
            if current.exists():
                shutil.move(str(current), str(backup / name))
                moved.append(name)
        for name in names:
            staged = new / name
            if staged.exists():
                shutil.move(str(staged), str(home / name))
                done.append(name)
    except OSError as exc:
        for name in done:       # 新代码退回暂存区
            with contextlib.suppress(OSError):
                shutil.move(str(home / name), str(new / name))
        for name in moved:      # 旧代码搬回原位
            with contextlib.suppress(OSError):
                shutil.move(str(backup / name), str(home / name))
        err = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "version": version,
               "error": f"{type(exc).__name__}: {exc}",
               "moved": moved, "done": done}
        with contextlib.suppress(OSError):
            (upd / "last-error.json").write_text(
                json.dumps(err, ensure_ascii=False) + "\n", encoding="utf-8")
        return {"ok": False, "error": f"替换失败，已回滚: {exc}"}

    flag.unlink(missing_ok=True)
    shutil.rmtree(new, ignore_errors=True)
    return {"ok": True, "version": version, "applied": done,
            "backup": str(backup),
            "note": f"已升级到 v{version}；旧代码在 data/bak/code-prev/，需要回滚就把它搬回来"}
