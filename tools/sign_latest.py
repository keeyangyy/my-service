"""给 latest.json 追加 Ed25519 签名（sig 字段）。

用法：
    set UPDATE_SIGNING_KEY=<私钥 hex>
    python tools/sign_latest.py dist/latest.json

签名覆盖 version / url / sha256 / size / notes_text 的规范化 JSON；
客户端用内置公钥验签，验不过就拒绝更新。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from core import ed25519  # noqa: E402
from core.update import canonical_payload  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="给 latest.json 追加签名")
    ap.add_argument("latest_json", help="latest.json 路径")
    args = ap.parse_args()

    key_hex = os.environ.get("UPDATE_SIGNING_KEY", "").strip()
    if not key_hex:
        print("缺少 UPDATE_SIGNING_KEY（私钥 hex）", file=sys.stderr)
        return 2
    try:
        seed = bytes.fromhex(key_hex)
    except ValueError:
        print("UPDATE_SIGNING_KEY 不是合法 hex", file=sys.stderr)
        return 2
    if len(seed) != 32:
        print("UPDATE_SIGNING_KEY 必须是 32 字节（64 位 hex）", file=sys.stderr)
        return 2

    path = Path(args.latest_json)
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        print("latest.json 根节点必须是对象", file=sys.stderr)
        return 2
    data["sig"] = ed25519.sign(seed, canonical_payload(data)).hex()
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    print("signed:", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())