"""my-service · core/update.py

便携包一键升级：检查 GitHub Release 的 `latest.json` → 下载 zip → 校验 sha256 →
解压到 `data/update/app.new/` → 写待生效标记。

**真正的目录替换由 launcher（包根 `start.cmd`）在下次启动 python 之前完成**：
Windows 上运行中的 `app/` 目录被 cwd/文件句柄占用，无法重命名；旧宿主退出、
新宿主尚未起来的那一刻才是唯一安全窗口。替换失败就保留标记，下次启动再试。
"""
from __future__ import annotations

import hashlib
import json
import shutil
import urllib.request
import zipfile
from pathlib import Path

DEFAULT_REPO = "keeyangyy/my-service"
TIMEOUT = 20
UA = "my-service-updater"
UPDATE_SUBDIR = Path("data") / "update"


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


def _pick_app_dir(staging: Path) -> Path | None:
    """zip 顶层是 my-service-x.y.z/，取其中的 app/（含 src/server.py 才算有效）。"""
    for top in staging.iterdir():
        candidate = top / "app" if top.is_dir() else None
        if candidate is not None and (candidate / "src" / "server.py").is_file():
            return candidate
    return None


def apply(home: Path, info: dict) -> dict:
    """下载 + 校验 + 解压到 data/update/app.new，并写 pending.json。

    返回 {ok, version, note} 或 {ok: False, error}。
    """
    version = str(info.get("version") or "")
    url = str(info.get("url") or "")
    sha256 = str(info.get("sha256") or "")
    if not version or not url:
        return {"ok": False, "error": "缺少 version 或 url"}

    upd = Path(home) / UPDATE_SUBDIR
    shutil.rmtree(upd / "staging", ignore_errors=True)
    shutil.rmtree(upd / "app.new", ignore_errors=True)
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

    app_src = _pick_app_dir(staging)
    if app_src is None:
        return {"ok": False, "error": "包内缺少 app/src/server.py"}
    target = upd / "app.new"
    shutil.move(str(app_src), str(target))
    shutil.rmtree(staging, ignore_errors=True)
    zip_path.unlink(missing_ok=True)

    flag = upd / "pending.json"
    flag.write_text(json.dumps({"version": version}, ensure_ascii=False) + "\n",
                    encoding="utf-8")
    return {"ok": True, "version": version, "note": "重启后由 start.cmd 替换 app/ 并生效"}


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
