"""HostController 自启开关：写/删启动文件夹 .lnk（临时 APPDATA 隔离，不动真实环境）。"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from core.host import HostController  # noqa: E402


def _read_shortcut(lnk: Path) -> tuple[str, str, str]:
    """用 WScript.Shell 读回快捷方式 → (TargetPath, Arguments, WorkingDirectory)。"""
    ps = (
        "$w = New-Object -ComObject WScript.Shell; "
        f"$s = $w.CreateShortcut({HostController._ps_quote(str(lnk))}); "
        "Write-Output ($s.TargetPath + '|@|' + $s.Arguments + '|@|' + $s.WorkingDirectory)"
    )
    done = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive",
                           "-Command", ps],
                          capture_output=True, text=True, timeout=30, check=True)
    target, args, workdir = done.stdout.strip().split("|@|")
    return target, args, workdir


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

    def _lnk(self) -> Path:
        path = HostController._startup_lnk_path()
        self.assertIsNotNone(path)
        return path

    def test_enable_creates_lnk_to_pythonw(self):
        ok, state = self.host.set_autostart(True)
        self.assertTrue(ok, state)
        self.assertEqual(state, "startup")
        lnk = self._lnk()
        self.assertTrue(lnk.is_file())
        target, args, workdir = _read_shortcut(lnk)
        self.assertIn("pythonw.exe", target.lower())      # 无控制台解释器，登录不闪窗
        self.assertIn("server.py", args)
        self.assertEqual(Path(workdir).name, "src")       # cwd 与手动启动一致

    def test_disable_removes_lnk(self):
        self.host.set_autostart(True)
        ok, state = self.host.set_autostart(False)
        self.assertTrue(ok)
        self.assertEqual(state, "none")
        self.assertFalse(self._lnk().exists())

    def test_legacy_vbs_and_cmd_are_removed(self):
        legacy = []
        for path in (HostController._startup_vbs_path(),
                     HostController._startup_cmd_path()):
            self.assertIsNotNone(path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("rem legacy\r\n", encoding="gbk")
            legacy.append(path)
        self.host.set_autostart(True)
        for path in legacy:
            self.assertFalse(path.exists(), f"历史方案 {path.name} 应被清理，避免双启动")

    def test_detect_autostart_sees_lnk(self):
        self.host.set_autostart(True)
        self.assertEqual(HostController._detect_autostart(), "startup")

    def test_bundled_runtime_pythonw_wins(self):
        """-WithRuntime 打包：包根 runtime\\pythonw.exe 优先于系统 pythonw。"""
        home = self.root / "pkg"
        (home / "src").mkdir(parents=True)
        (home / "runtime").mkdir()
        (home / "runtime" / "pythonw.exe").write_text("", encoding="ascii")
        host = HostController(None, self.root / "logs", self.root / "data",
                              app_dir=home, home=home)
        ok, _ = host.set_autostart(True)
        self.assertTrue(ok)
        target, _, _ = _read_shortcut(self._lnk())
        self.assertIn("runtime\\pythonw.exe", target.lower())


if __name__ == "__main__":
    unittest.main(verbosity=2)
