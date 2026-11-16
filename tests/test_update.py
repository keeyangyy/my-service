"""update 模块：版本比较 + 本地 zip 暂存（code.new）+ 启动前按清单替换/保留数据。"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from core import update  # noqa: E402


class _FakeResp:
    """urlopen 的最小替身：只实现 _get_json 用到的 read 与上下文管理。"""

    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *args) -> bool:
        return False


def _make_zip(path: Path, version: str, layout: str = "root") -> None:
    """最小升级包。三种布局：

    - root：`my-service-<版本>/src/...`（构建脚本当前产物）
    - flat：`src/...` 直接在 zip 顶层（v0.17.1 及以前的产物）
    - app： `my-service-<版本>/app/src/...`（旧布局）

    必须带 `src/core/`：替换以「目录」为单位，且会校验 src 完整性，残缺 src 会被拒绝。
    """
    if layout == "app":
        prefix = f"my-service-{version}/app/"
    elif layout == "root":
        prefix = f"my-service-{version}/"
    else:  # flat
        prefix = ""
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(f"{prefix}src/server.py", "print('new')\n")
        zf.writestr(f"{prefix}src/core/host.py", 'VERSION = "9.9.9"\n')
        zf.writestr(f"{prefix}README.md", "new readme\n")
        zf.writestr(f"{prefix}docs/guide/usage.md", "new guide\n")
        zf.writestr(f"{prefix}data/logs/ignore.log", "must not be installed\n")


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

    def test_apply_accepts_flat_root_layout(self):
        """v0.17.1 及以前的包没有顶层目录，升级也必须认。"""
        result = self._stage(version="9.9.9", layout="flat")
        self.assertTrue(result["ok"], result)
        staged = self.home / "data" / "update" / "code.new"
        self.assertTrue((staged / "src" / "server.py").is_file())

    def test_apply_pending_tolerates_cwd_inside_src(self):
        """更新器曾以 src 为 cwd；Windows 下不能移动进程当前目录，必须先切走。"""
        self._stage()
        src = self.home / "src"
        old = Path.cwd()
        try:
            os.chdir(src)
            result = update.apply_pending(self.home)
        finally:
            os.chdir(old)
        self.assertTrue(result["ok"], result)
        self.assertTrue((self.home / "src" / "server.py").is_file())

    def test_apply_pending_refuses_concurrent_run(self):
        """宿主派生的更新器与手动 start.ps1 可能同时进来。"""
        self._stage()
        lock = self.home / "data" / "update" / "apply.lock"
        lock.write_text("", encoding="utf-8")
        result = update.apply_pending(self.home)
        self.assertFalse(result["ok"])
        self.assertIn("另一个升级进程", result["error"])

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

    # ---- 代理与校验 ----
    def test_candidate_urls_proxy_first(self):
        url = "https://github.com/x/y/releases/download/v1/z.zip"
        urls = update.candidate_urls(url, mirrors=["https://m1", "https://m2"])
        self.assertEqual(urls[0], "https://m1/" + url)
        self.assertEqual(urls[1], "https://m2/" + url)
        self.assertEqual(urls[-1], url)          # 官方兜底

    def test_candidate_urls_without_mirrors(self):
        url = "https://github.com/x/y/releases/download/v1/z.zip"
        self.assertEqual(update.candidate_urls(url, mirrors=[]), [url])

    def test_candidate_urls_do_not_proxy_file_urls(self):
        """离线包（file://）不套代理前缀，避免无意义的网络请求。"""
        self.assertEqual(update.candidate_urls("file:///tmp/pkg.zip"), ["file:///tmp/pkg.zip"])

    def test_check_uses_mirror_first_and_returns_notes(self):
        from core import ed25519

        seed = b"\x04" * 32
        url = ("https://github.com/keeyangyy/my-service/releases/download/"
               "v9.9.9/my-service-9.9.9.zip")
        data = {
            "version": "9.9.9", "url": url, "sha256": "a" * 64,
            "size": 123, "notes": "notes-url", "notes_text": "更新说明",
        }
        data["sig"] = ed25519.sign(seed, update.canonical_payload(data)).hex()
        payload = json.dumps(data).encode("utf-8")
        calls: list[str] = []

        def fake_urlopen(req, timeout=None):
            calls.append(req.full_url)
            return _FakeResp(payload)

        with mock.patch.object(update.update_pubkey, "PUBLIC_KEY_HEX",
                               ed25519.public_key(seed).hex()), \
                mock.patch("core.update.urllib.request.urlopen", fake_urlopen):
            info = update.check("0.1.0", mirrors=["https://mirror.example"])
        self.assertTrue(info["has_update"])
        self.assertEqual(info["notes_text"], "更新说明")
        self.assertTrue(info["source"].startswith("https://mirror.example/"))
        self.assertTrue(calls[0].startswith("https://mirror.example/https://github.com/"))

    def test_check_rejects_foreign_url(self):
        payload = json.dumps({
            "version": "9.9.9", "url": "https://evil.example/x.zip",
            "sha256": "a" * 64, "size": 1,
        }).encode("utf-8")
        with mock.patch("core.update.urllib.request.urlopen",
                        lambda req, timeout=None: _FakeResp(payload)), \
                self.assertRaises(ValueError):
            update.check("0.1.0", mirrors=[])

    def test_check_rejects_bad_sha(self):
        url = ("https://github.com/keeyangyy/my-service/releases/download/"
               "v9.9.9/my-service-9.9.9.zip")
        payload = json.dumps({"version": "9.9.9", "url": url,
                              "sha256": "not-a-sha", "size": 1}).encode("utf-8")
        with mock.patch("core.update.urllib.request.urlopen",
                        lambda req, timeout=None: _FakeResp(payload)), \
                self.assertRaises(ValueError):
            update.check("0.1.0", mirrors=[])

    # ---- 签名（Ed25519）----
    def _signed_latest(self, seed: bytes) -> dict:
        version = "9.9.9"
        data = {
            "version": version,
            "url": ("https://github.com/keeyangyy/my-service/releases/download/"
                    f"v{version}/my-service-{version}.zip"),
            "sha256": "a" * 64,
            "size": 123,
            "notes_text": "更新说明",
        }
        from core import ed25519

        data["sig"] = ed25519.sign(seed, update.canonical_payload(data)).hex()
        return data

    def test_check_accepts_valid_signature(self):
        from core import ed25519

        seed = b"\x01" * 32
        pub = ed25519.public_key(seed)
        payload = json.dumps(self._signed_latest(seed)).encode("utf-8")
        with mock.patch.object(update.update_pubkey, "PUBLIC_KEY_HEX", pub.hex()), \
                mock.patch("core.update.urllib.request.urlopen",
                           lambda req, timeout=None: _FakeResp(payload)):
            info = update.check("0.1.0", mirrors=[])
        self.assertTrue(info["has_update"])

    def test_check_rejects_missing_signature(self):
        data = self._signed_latest(b"\x02" * 32)
        data.pop("sig")
        payload = json.dumps(data).encode("utf-8")
        with mock.patch("core.update.urllib.request.urlopen",
                        lambda req, timeout=None: _FakeResp(payload)), \
                self.assertRaises(ValueError) as ctx:
            update.check("0.1.0", mirrors=[])
        self.assertIn("签名", str(ctx.exception))

    def test_check_rejects_bad_signature(self):
        data = self._signed_latest(b"\x03" * 32)
        data["sig"] = "0" * 128
        payload = json.dumps(data).encode("utf-8")
        with mock.patch("core.update.urllib.request.urlopen",
                        lambda req, timeout=None: _FakeResp(payload)), \
                self.assertRaises(ValueError) as ctx:
            update.check("0.1.0", mirrors=[])
        self.assertIn("签名", str(ctx.exception))

    def test_apply_rejects_untrusted_url(self):
        result = update.apply(self.home, {
            "version": "9.9.9", "url": "https://evil.example/x.zip",
            "sha256": "a" * 64, "size": 1})
        self.assertFalse(result["ok"])
        self.assertIn("本仓库", result["error"])

    def test_apply_rejects_bad_size(self):
        zip_path = self.root / "pkg.zip"
        _make_zip(zip_path, "9.9.9")
        sha = hashlib.sha256(zip_path.read_bytes()).hexdigest()
        result = update.apply(self.home, {
            "version": "9.9.9", "url": zip_path.as_uri(),
            "sha256": sha, "size": zip_path.stat().st_size + 1})
        self.assertFalse(result["ok"])
        self.assertIn("大小", result["error"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
