"""my-service · core/api/static.py

web/ 静态资源读取（含目录逃逸防护）。

WEB_DIR 定位：core/api/static.py → 上三级是服务根（my-service/），故指向根下的 web/。
"""
from __future__ import annotations

from pathlib import Path

WEB_DIR = Path(__file__).resolve().parents[3] / "web"

MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".png": "image/png", ".ico": "image/x-icon",
}


def load(rel: str) -> tuple[bytes, str] | None:
    """读取 WEB_DIR 下的相对文件 → (bytes, mime)；不存在或越界返回 None。"""
    path = (WEB_DIR / rel).resolve()
    if not path.is_file() or WEB_DIR.resolve() not in path.parents:
        return None
    return path.read_bytes(), MIME.get(path.suffix.lower(), "application/octet-stream")
