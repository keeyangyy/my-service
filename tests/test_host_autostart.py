"""HostController 自启开关：写/删启动文件夹 vbs（临时 APPDATA 隔离，不动真实环境）。"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from core.host import HostController  # noqa: E402


class TestHostAutostart(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="ms-auto-"))
        (self.root / "appdata").mkdir()
        self.old_appdata = os.environ.get("APPDATA")
        os.environ["APPDATA"] = str(self.root / "appdata")
        self.host = HostController(None, self.root / "logs", self.root / "data",
                                   app_dir=self.root / "app", home=self.root / "app")

    def tearDown(self):
        if self.old_appdata is None:
            os.environ.pop("APPDATA", None)
        else:
            os.environ["APPDATA"] = self.old_appdata
        shutil.rmtree(self.root, ignore_errors=True)

    def _vbs_text(self) -> str:
        path = HostController._startup_vbs_path()
        self.assertIsNotNone(path)
        return path.read_bytes().decode("gbk")

    def test_enable_creates_windowless_vbs(self):
        ok, state = self.host.set_autostart(True)
        self.assertTrue(ok, state)
        self.assertEqual(state, "startup")
        text = self._vbs_text()
        self.assertIn("pythonw.exe", text)
        self.assertIn("server.py", text)
        self.assertIn(", 0, False", text)      # SW_HIDE：不弹窗口
        self.assertIn('"""', text)             # 路径已加引号

    def test_disable_removes_vbs(self):
        self.host.set_autostart(True)
        ok, state = self.host.set_autostart(False)
        self.assertTrue(ok)
        self.assertEqual(state, "none")
        path = HostController._startup_vbs_path()
        self.assertIsNotNone(path)
        self.assertFalse(path.exists())

    def test_legacy_cmd_is_removed(self):
        legacy = HostController._startup_cmd_path()
        self.assertIsNotNone(legacy)
        legacy.parent.mkdir(parents=True, exist_ok=True)
        legacy.write_text("@echo off\r\n", encoding="gbk")
        self.host.set_autostart(True)
        self.assertFalse(legacy.exists(), "旧 cmd 方案应被清理，避免双启动")

    def test_portable_uses_runtime_pythonw(self):
        home = self.root / "pkg"
        (home / "app" / "src").mkdir(parents=True)
        (home / "runtime").mkdir()
        (home / "runtime" / "pythonw.exe").write_text("", encoding="ascii")
        host = HostController(None, self.root / "logs", self.root / "data",
                              app_dir=home / "app", home=home)
        ok, _ = host.set_autostart(True)
        self.assertTrue(ok)
        self.assertIn("runtime\\pythonw.exe", self._vbs_text())


if __name__ == "__main__":
    unittest.main(verbosity=2)
