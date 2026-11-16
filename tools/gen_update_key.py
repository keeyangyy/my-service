"""生成更新清单签名密钥对（Ed25519）。

用法：
    python tools/gen_update_key.py --out <私钥文件路径>

私钥写入 --out（hex，一行，600 权限视平台而定），公钥打印到标准输出。
把公钥写入 src/core/update_pubkey.py；把私钥设置为 GitHub Actions secret
UPDATE_SIGNING_KEY（或本地签名时临时放进环境变量）。
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from core import ed25519  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="生成 Ed25519 更新签名密钥对")
    ap.add_argument("--out", required=True, help="私钥保存路径（hex 一行）")
    args = ap.parse_args()

    out = Path(args.out).expanduser()
    seed = os.urandom(32)
    public = ed25519.public_key(seed)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(seed.hex() + "\n", encoding="ascii")
    print("public key (hex):", public.hex())
    print("private key saved:", out)
    print("下一步：公钥写入 src/core/update_pubkey.py；私钥设为 GitHub secret")
    print("        UPDATE_SIGNING_KEY（本地签名可临时 export UPDATE_SIGNING_KEY）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())