"""update 模块：版本比较 + 用本地 zip 走一遍下载 / 校验 / 暂存。"""
from __future__ import annotations

import hashlib
import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from core import update  # noqa: E402


def _make_zip(path: Path, version: str) -> None:
    """构造 my-service-<version>/app/src/server.py 结构的最小升级包。"""
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(f"my-service-{version}/app/src/server.py", "print('new')\n")
        zf.writestr(f"my-service-{version}/app/README.md", "new\n")


class TestUpdate(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="ms-upd-"))
        self.home = self.root / "pkg"
        (self.home / "app" / "src").mkdir(parents=True)
        (self.home / "app" / "src" / "server.py").write_text(
            "print('old')\n", encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_version_compare(self):
        self.assertTrue(update.is_newer("0.14.0", "0.13.0"))
        self.assertFalse(update.is_newer("0.9.0", "0.10.0"))
        self.assertFalse(update.is_newer("0.13.0", "0.13.0"))

    def test_apply_stages_package(self):
        zip_path = self.root / "pkg.zip"
        _make_zip(zip_path, "9.9.9")
        sha = hashlib.sha256(zip_path.read_bytes()).hexdigest()
        result = update.apply(self.home, {
            "version": "9.9.9", "url": zip_path.as_uri(), "sha256": sha})
        self.assertTrue(result["ok"], result)
        staged = self.home / "data" / "update" / "app.new" / "src" / "server.py"
        self.assertTrue(staged.is_file())
        self.assertEqual(update.pending_version(self.home), "9.9.9")

    def test_apply_rejects_bad_sha(self):
        zip_path = self.root / "pkg.zip"
        _make_zip(zip_path, "9.9.9")
        result = update.apply(self.home, {
            "version": "9.9.9", "url": zip_path.as_uri(), "sha256": "0" * 64})
        self.assertFalse(result["ok"])
        self.assertIsNone(update.pending_version(self.home))


if __name__ == "__main__":
    unittest.main(verbosity=2)
