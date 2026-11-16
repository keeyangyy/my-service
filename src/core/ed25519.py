"""my-service · core/ed25519.py

Ed25519 签名/验签（RFC 8032）的最小纯标准库实现，**只用于更新清单防篡改**：

- 发布流程用私钥签 `latest.json`（`sig` 字段）
- 客户端用内置公钥验签；验签失败即拒绝更新

安全边界：纯 Python、非常数时间实现，目标是「代理/中间人替换清单能被发现」，
不是通用密码学库；不要用它保护高价值密钥或大流量数据。
"""
from __future__ import annotations

import hashlib

_P = 2**255 - 19
_L = 2**252 + 27742317777372353535851937790883648493
_D = -121665 * pow(121666, _P - 2, _P) % _P
_I = pow(2, (_P - 1) // 4, _P)


def _inv(x: int) -> int:
    return pow(x, _P - 2, _P)


def _recover_x(y: int, sign: int) -> int | None:
    if y >= _P:
        return None
    x2 = (y * y - 1) * _inv(_D * y * y + 1) % _P
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (_P + 3) // 8, _P)
    if (x * x - x2) % _P != 0:
        x = x * _I % _P
    if (x * x - x2) % _P != 0:
        return None
    if (x & 1) != sign:
        x = _P - x
    return x


_GY = 4 * _inv(5) % _P
_GX = _recover_x(_GY, 0)
_G = (_GX, _GY, 1, _GX * _GY % _P)
_ZERO = (0, 1, 1, 0)


def _add(p1: tuple, p2: tuple) -> tuple:
    x1, y1, z1, t1 = p1
    x2, y2, z2, t2 = p2
    a = (y1 - x1) * (y2 - x2) % _P
    b = (y1 + x1) * (y2 + x2) % _P
    c = t1 * 2 * _D * t2 % _P
    d = z1 * 2 * z2 % _P
    e, f, g, h = b - a, d - c, d + c, b + a
    return (e * f % _P, g * h % _P, f * g % _P, e * h % _P)


def _mul(s: int, point: tuple) -> tuple:
    q = _ZERO
    while s > 0:
        if s & 1:
            q = _add(q, point)
        point = _add(point, point)
        s >>= 1
    return q


def _encode(point: tuple) -> bytes:
    x, y, z, _t = point
    zi = _inv(z)
    x = x * zi % _P
    y = y * zi % _P
    return int.to_bytes(y | ((x & 1) << 255), 32, "little")


def _decode(data: bytes) -> tuple | None:
    if len(data) != 32:
        return None
    y = int.from_bytes(data, "little")
    sign = y >> 255
    y &= (1 << 255) - 1
    x = _recover_x(y, sign)
    if x is None:
        return None
    return (x, y, 1, x * y % _P)


def _expand(seed: bytes) -> tuple[int, bytes]:
    h = hashlib.sha512(seed).digest()
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= (1 << 254)
    return a, h[32:]


def public_key(seed: bytes) -> bytes:
    """由 32 字节私钥种子导出 32 字节公钥。"""
    if len(seed) != 32:
        raise ValueError("Ed25519 私钥种子必须是 32 字节")
    a, _prefix = _expand(seed)
    return _encode(_mul(a, _G))


def sign(seed: bytes, msg: bytes) -> bytes:
    """私钥签名 → 64 字节签名。"""
    a, prefix = _expand(seed)
    pub = _encode(_mul(a, _G))
    r = int.from_bytes(hashlib.sha512(prefix + msg).digest(), "little") % _L
    r_enc = _encode(_mul(r, _G))
    h = int.from_bytes(hashlib.sha512(r_enc + pub + msg).digest(), "little") % _L
    s = (r + h * a) % _L
    return r_enc + int.to_bytes(s, 32, "little")


def verify(public: bytes, msg: bytes, sig: bytes) -> bool:
    """公钥验签：任何格式问题一律返回 False。"""
    if len(public) != 32 or len(sig) != 64:
        return False
    a_point = _decode(public)
    r_point = _decode(sig[:32])
    if a_point is None or r_point is None:
        return False
    s = int.from_bytes(sig[32:], "little")
    if s >= _L:
        return False
    h = int.from_bytes(hashlib.sha512(sig[:32] + public + msg).digest(),
                      "little") % _L
    return _encode(_mul(s, _G)) == _encode(_add(r_point, _mul(h, a_point)))
