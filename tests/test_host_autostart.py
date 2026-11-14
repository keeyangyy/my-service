"""HostController 自启开关：写/删启动文件夹 cmd（用临时 APPDATA 隔离，不动真实环境）。"""
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

    def _cmd_text(self) -> str:
        path = HostController._startup_cmd_path()
        self.assertIsNotNone(path)
        return path.read_bytes().decode("gbk")

    def test_enable_creates_startup_cmd(self):
        ok, state = self.host.set_autostart(True)
        self.assertTrue(ok, state)
        self.assertEqual(state, "startup")
        text = self._cmd_text()
        self.assertIn("run\\start.ps1", text)
        self.assertIn("\r\n", text)

    def test_disable_removes_startup_cmd(self):
        self.host.set_autostart(True)
        ok, state = self.host.set_autostart(False)
        self.assertTrue(ok)
        self.assertEqual(state, "none")
        path = HostController._startup_cmd_path()
        self.assertIsNotNone(path)
        self.assertFalse(path.exists())

    def test_portable_launcher_points_to_package_root(self):
        home = self.root / "pkg"
        (home / "app").mkdir(parents=True)
        (home / "start.cmd").write_text("", encoding="ascii")
        host = HostController(None, self.root / "logs", self.root / "data",
                              app_dir=home / "app", home=home)
        ok, _ = host.set_autostart(True)
        self.assertTrue(ok)
        text = self._cmd_text()
        self.assertIn("start.cmd", text)
        self.assertNotIn("start.ps1", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
