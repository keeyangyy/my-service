"""update 模块：版本比较 + 本地 zip 暂存（code.new）+ 启动前按清单替换/保留数据。"""
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


def _make_zip(path: Path, version: str, layout: str = "root") -> None:
    """最小升级包。root = 包根即代码根（新布局）；app = 代码在 app/ 里（旧布局）。

    必须带 `src/core/`：替换以「目录」为单位，且会校验 src 完整性，残缺 src 会被拒绝。
    """
    base = f"my-service-{version}" if layout == "root" else f"my-service-{version}/app"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(f"{base}/src/server.py", "print('new')\n")
        zf.writestr(f"{base}/src/core/host.py", 'VERSION = "9.9.9"\n')
        zf.writestr(f"{base}/README.md", "new readme\n")
        zf.writestr(f"{base}/docs/guide/usage.md", "new guide\n")
        zf.writestr(f"{base}/data/logs/ignore.log", "must not be installed\n")


class TestUpdate(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="ms-upd-"))
        self.home = self.root / "pkg"
        # 旧代码 + 用户数据：只有 src/web/文档 该被换掉，reg/config/data 必须原样
        (self.home / "src").mkdir(parents=True)
        (self.home / "src" / "server.py").write_text("print('old')\n", encoding="utf-8")
        (self.home / "web").mkdir()
        (self.home / "web" / "app.js").write_text("// old\n", encoding="utf-8")
        (self.home / "README.md").write_text("old readme\n", encoding="utf-8")
        (self.home / "reg" / "services").mkdir(parents=True)
        (self.home / "reg" / "services" / "s.json").write_text("{}", encoding="utf-8")
        (self.home / "config.json").write_text("{}", encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _stage(self, version: str = "9.9.9", layout: str = "root") -> dict:
        zip_path = self.root / "pkg.zip"
        _make_zip(zip_path, version, layout)
        sha = hashlib.sha256(zip_path.read_bytes()).hexdigest()
        return update.apply(self.home, {
            "version": version, "url": zip_path.as_uri(), "sha256": sha})

    def test_version_compare(self):
        self.assertTrue(update.is_newer("0.14.0", "0.13.0"))
        self.assertFalse(update.is_newer("0.9.0", "0.10.0"))
        self.assertFalse(update.is_newer("0.13.0", "0.13.0"))

    def test_apply_stages_code_only(self):
        result = self._stage()
        self.assertTrue(result["ok"], result)
        staged = self.home / "data" / "update" / "code.new"
        self.assertTrue((staged / "src" / "server.py").is_file())
        self.assertTrue((staged / "README.md").is_file())
        self.assertTrue((staged / "docs" / "guide" / "usage.md").is_file())
        # 包里混进来的 data/ 不该进暂存区（升级不动数据）
        self.assertFalse((staged / "data").exists())
        self.assertEqual(update.pending_version(self.home), "9.9.9")

    def test_apply_accepts_legacy_app_layout(self):
        result = self._stage(version="9.9.9", layout="app")
        self.assertTrue(result["ok"], result)
        staged = self.home / "data" / "update" / "code.new"
        self.assertTrue((staged / "src" / "server.py").is_file())
        self.assertFalse((staged / "app").exists())

    def test_apply_rejects_bad_sha(self):
        zip_path = self.root / "pkg.zip"
        _make_zip(zip_path, "9.9.9")
        result = update.apply(self.home, {
            "version": "9.9.9", "url": zip_path.as_uri(), "sha256": "0" * 64})
        self.assertFalse(result["ok"])
        self.assertIsNone(update.pending_version(self.home))

    def test_apply_pending_swaps_code_and_keeps_data(self):
        self._stage()
        result = update.apply_pending(self.home)
        self.assertTrue(result["ok"], result)
        self.assertIn("src", result["applied"])
        self.assertEqual((self.home / "src" / "server.py").read_text(encoding="utf-8"),
                         "print('new')\n")
        self.assertEqual((self.home / "README.md").read_text(encoding="utf-8"),
                         "new readme\n")
        # 旧代码留痕可回滚
        prev = self.home / "data" / "bak" / "code-prev"
        self.assertEqual((prev / "src" / "server.py").read_text(encoding="utf-8"),
                         "print('old')\n")
        # 用户数据与注册条原样
        self.assertTrue((self.home / "reg" / "services" / "s.json").is_file())
        self.assertTrue((self.home / "config.json").is_file())
        # 标记与暂存区清理干净
        self.assertIsNone(update.pending_version(self.home))
        self.assertFalse((self.home / "data" / "update" / "code.new").exists())

    def test_apply_pending_without_staging_is_reported(self):
        result = update.apply_pending(self.home)
        self.assertFalse(result["ok"])
        self.assertIn("code.new", result["error"])

    def test_apply_pending_only_touches_staged_entries(self):
        """暂存区只带 src 时，web/run/docs 原样留着（不能因为"不在包里"就被搬走）。"""
        (self.home / "run").mkdir()
        (self.home / "run" / "start.ps1").write_text("# old run\n", encoding="utf-8")
        staged = self.home / "data" / "update" / "code.new"
        (staged / "src" / "core").mkdir(parents=True)
        (staged / "src" / "server.py").write_text("print('new')\n", encoding="utf-8")
        result = update.apply_pending(self.home)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["applied"], ["src"])
        self.assertTrue((self.home / "web" / "app.js").is_file())
        self.assertTrue((self.home / "run" / "start.ps1").is_file())

    def test_apply_pending_rejects_incomplete_src(self):
        """暂存的 src 缺 core/ 时拒绝替换——否则整棵树会被换残废。"""
        staged = self.home / "data" / "update" / "code.new"
        (staged / "src").mkdir(parents=True)
        (staged / "src" / "server.py").write_text("print('broken')\n", encoding="utf-8")
        before = (self.home / "src" / "server.py").read_text(encoding="utf-8")
        result = update.apply_pending(self.home)
        self.assertFalse(result["ok"])
        self.assertIn("不完整", result["error"])
        self.assertEqual((self.home / "src" / "server.py").read_text(encoding="utf-8"),
                         before, "拒绝替换时原代码必须原样")


if __name__ == "__main__":
    unittest.main(verbosity=2)
