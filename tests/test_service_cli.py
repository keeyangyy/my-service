"""service_cli：纯函数、pid 判定与 shortcut 生成（不启动真实宿主）。"""
from __future__ import annotations

import contextlib
import io
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from service_cli import (  # noqa: E402
    cmd_shortcut,
    cmd_status,
    interpreter,
    is_running,
    load_config,
    pid_alive,
    pid_file,
    read_pid,
    resolve_data_root,
    shortcut_path,
    version,
)


class TestServiceCli(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="ms-cli-"))
        self.app = self.root / "app"
        (self.app / "src" / "core").mkdir(parents=True)
        (self.app / "src" / "core" / "host.py").write_text(
            'VERSION = "9.9.9"\n', encoding="utf-8")
        self.old_home = os.environ.pop("MY_SERVICE_HOME", None)

    def tearDown(self):
        if self.old_home is not None:
            os.environ["MY_SERVICE_HOME"] = self.old_home
        shutil.rmtree(self.root, ignore_errors=True)

    def test_resolve_data_root_source_mode(self):
        self.assertEqual(resolve_data_root(self.app), self.app)

    def test_resolve_data_root_ignores_runtime(self):
        """包根就是代码根：打包（哪怕带 runtime）也不改变数据根。"""
        (self.app / "runtime").mkdir()
        (self.app / "runtime" / "python.exe").write_text("", encoding="ascii")
        self.assertEqual(resolve_data_root(self.app), self.app)

    def test_resolve_data_root_env_wins(self):
        custom = self.root / "custom"
        os.environ["MY_SERVICE_HOME"] = str(custom)
        self.assertEqual(resolve_data_root(self.app), custom.resolve())

    def test_shortcut_path_source_mode(self):
        self.assertEqual(shortcut_path(self.app, self.app),
                         self.app / "run" / "Dashboard.url")

    def test_shortcut_path_always_under_run(self):
        """快捷方式固定落 run/（每台机器生成；升级替换 run/ 后自动重建）。"""
        self.assertEqual(shortcut_path(self.app, self.root),
                         self.app / "run" / "Dashboard.url")

    def test_interpreter_prefers_bundled_pythonw(self):
        """-WithRuntime 打包时包内自带 pythonw，优先用它。"""
        (self.app / "runtime").mkdir()
        pythonw = self.app / "runtime" / "pythonw.exe"
        pythonw.write_text("", encoding="ascii")
        self.assertEqual(interpreter(self.app, self.app), pythonw)

    def test_read_pid_and_alive_for_self(self):
        (self.root / "data").mkdir()
        pid_file(self.root).write_text(str(os.getpid()), encoding="ascii")
        self.assertEqual(read_pid(self.root), os.getpid())
        self.assertTrue(pid_alive(os.getpid()))
        self.assertEqual(is_running(self.root), os.getpid())

    def test_dead_pid_is_not_running(self):
        (self.root / "data").mkdir()
        pid_file(self.root).write_text("999999", encoding="ascii")
        self.assertFalse(pid_alive(999999))
        self.assertIsNone(is_running(self.root))

    def test_version_reads_constant(self):
        self.assertEqual(version(self.app), "9.9.9")

    def test_load_config_defaults(self):
        self.assertEqual(load_config(self.root),
                         {"host": "127.0.0.1", "port": 1236})

    def test_shortcut_written(self):
        with contextlib.redirect_stdout(io.StringIO()):
            code = cmd_shortcut(self.app, self.app)
        self.assertEqual(code, 0)
        path = self.app / "run" / "Dashboard.url"
        self.assertTrue(path.is_file())
        self.assertIn("URL=http://127.0.0.1:1236",
                      path.read_bytes().decode("ascii"))

    def test_status_reports_stopped(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = cmd_status(self.app, self.app)
        self.assertEqual(code, 1)
        self.assertIn("stopped v9.9.9", buf.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=2)
