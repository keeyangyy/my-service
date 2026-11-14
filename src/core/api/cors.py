# -*- coding: utf-8 -*-
"""my-service · core/api/cors.py

CORS / 外站门禁的纯判定。无 IO，便于单测与复用。

背景：控制面绑定 127.0.0.1，本机任何程序（curl / 脚本 / 服务）本来就能直接调用，
从来不需要额外开放。唯一受限的是"浏览器里的网页"——跨源时浏览器默认不把响应
交给页面 JS，带 JSON 的请求还会先发 OPTIONS 预检、没被应答就根本不发。
方案 S：判据只有一条"来源是否本机"——本机网页（任意端口）全放行且能读回结果；
外站网页一律拒绝（浏览器跨源 POST 必定带 Origin，故可可靠识别；不带 Origin 的
请求即 curl / 脚本 / 同源面板，按本机程序处理，行为与历史完全一致）。
"""
from __future__ import annotations

from urllib.parse import urlparse

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]"}

# 会改变宿主或服务状态的接口（外站门禁只拦这些；读接口无需拦，读也读不到）
WRITE_PREFIXES = (
    "/api/services/", "/api/crons/", "/api/audit/clear", "/api/config/", "/api/host/",
)


def origin_of(headers) -> str | None:
    """取出请求的 Origin；不存在返回 None（同源请求 / curl / 非浏览器客户端）。"""
    origin = (headers.get("Origin") or "").strip()
    return origin or None


def origin_host(origin: str) -> str:
    """由 Origin 解析主机名（小写，去端口）。解析不出来返回空串。"""
    try:
        host = (urlparse(origin).hostname or "").lower()
    except ValueError:
        return ""
    return host


def is_local_origin(origin: str) -> bool:
    """来源是否为本机。端口不限——本机任何端口起的页面都算自己人。"""
    return origin_host(origin) in LOCAL_HOSTS


def is_write_path(path: str) -> bool:
    return path.startswith(WRITE_PREFIXES)
