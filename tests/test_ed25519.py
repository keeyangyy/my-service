"""core/ed25519.py：RFC 8032 测试向量与签名往返。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from core import ed25519  # noqa: E402

VECTORS = [
    (
        "9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60",
        "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a",
        b"",
        "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e06522490155"
        "5fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b",
    ),
    (
        "4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb",
        "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c",
        b"\x72",
        "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da"
        "085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00",
    ),
]


class TestEd25519(unittest.TestCase):
    def test_rfc8032_vectors(self):
        for seed_hex, pub_hex, msg, sig_hex in VECTORS:
            seed = bytes.fromhex(seed_hex)
            self.assertEqual(ed25519.public_key(seed).hex(), pub_hex)
            self.assertEqual(ed25519.sign(seed, msg).hex(), sig_hex)
            self.assertTrue(ed25519.verify(bytes.fromhex(pub_hex), msg,
                                           bytes.fromhex(sig_hex)))

    def test_roundtrip_and_tamper(self):
        seed = b"\x07" * 32
        pub = ed25519.public_key(seed)
        msg = b"latest.json payload"
        sig = ed25519.sign(seed, msg)
        self.assertTrue(ed25519.verify(pub, msg, sig))
        self.assertFalse(ed25519.verify(pub, msg + b"x", sig))
        bad = bytearray(sig)
        bad[0] ^= 1
        self.assertFalse(ed25519.verify(pub, msg, bytes(bad)))

    def test_bad_lengths_rejected(self):
        self.assertFalse(ed25519.verify(b"", b"m", b"\x00" * 64))
        self.assertFalse(ed25519.verify(b"\x00" * 32, b"m", b"\x00" * 63))
        with self.assertRaises(ValueError):
            ed25519.public_key(b"short")


if __name__ == "__main__":
    unittest.main(verbosity=2)
