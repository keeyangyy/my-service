"""service_cli：纯函数、pid 判定与 shortcut 生成（不启动真实宿主）。"""
from __future__ import annotations

import contextlib
import io
import os
import shutil
import sys
import tempfile
import unittest
import urllib.error
from unittest import mock
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from service_cli import (  # noqa: E402
    _api,
    base_url,
    client_host,
    _fmt_last,
    cmd_cron_run,
    cmd_crons,
    cmd_events,
    cmd_host_err,
    cmd_ls,
    cmd_shortcut,
    cmd_status,
    cmd_svc_restart,
    cmd_svc_start,
    cmd_svc_stop,
    interpreter,
    is_running,
    load_config,
    main,
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


class TestServiceCommands(unittest.TestCase):
    """服务管理命令：HTTP 调用与输出渲染（mock _api，不依赖真实宿主）。"""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="ms-cli-cmd-"))
        self.app = self.root / "app"
        (self.app / "src" / "core").mkdir(parents=True)
        (self.app / "src" / "core" / "host.py").write_text(
            'VERSION = "9.9.9"\n', encoding="utf-8")
        (self.root / "config.json").write_text(
            '{"host": "127.0.0.1", "port": 19999}', encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _run(self, fn, *args):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = fn(self.app, self.root, *args)
        return code, buf.getvalue()

    def test_ls_renders_rows(self):
        payload = {"services": [
            {"name": "svc-a", "display": "A", "status": "running", "port": 1234,
             "uptime_sec": 60, "autostart": True, "error": None},
            {"name": "svc-b", "display": "B", "status": "stopped", "port": None,
             "uptime_sec": None, "autostart": False, "error": "boom"},
        ]}
        with mock.patch("service_cli._api", return_value=payload):
            code, out = self._run(cmd_ls)
        self.assertEqual(code, 0)
        self.assertIn("svc-a", out)
        self.assertIn("autostart", out)
        self.assertIn("boom", out)

    def test_svc_actions_paths(self):
        with mock.patch("service_cli._api",
                        return_value={"status": "running", "message": "ok"}) as api:
            for fn in (cmd_svc_start, cmd_svc_stop, cmd_svc_restart):
                code, out = self._run(fn, "svc-a")
                self.assertEqual(code, 0)
                self.assertIn("svc-a -> running (ok)", out)
        paths = [c.args[2] for c in api.call_args_list]
        self.assertIn("/api/services/svc-a/start?via=cli", paths)
        self.assertIn("/api/services/svc-a/stop", paths)
        self.assertIn("/api/services/svc-a/restart", paths)

    def test_svc_action_requires_name(self):
        with self.assertRaises(SystemExit) as ctx:
            self._run(cmd_svc_start, None)
        self.assertEqual(ctx.exception.code, 2)

    def test_svc_action_error_returns_nonzero(self):
        with mock.patch("service_cli._api",
                        return_value={"ok": False, "status": "failed", "message": "no"}):
            code, _ = self._run(cmd_svc_stop, "svc-a")
        self.assertEqual(code, 1)

    def test_crons_render_and_fmt_last(self):
        payload = {"crons": [
            {"name": "c1", "display": "C1", "valid": True, "running": True,
             "enabled": True, "schedule": "daily 09:30", "last_run": 1,
             "last_ok": True, "last_dur_ms": 12},
            {"name": "c2", "display": "C2", "valid": False, "error": "bad"},
            {"name": "c3", "display": "C3", "valid": True, "enabled": False,
             "last_run": 2, "last_ok": False, "last_rc": None},
        ]}
        with mock.patch("service_cli._api", return_value=payload):
            code, out = self._run(cmd_crons)
        self.assertEqual(code, 0)
        self.assertIn("running", out)
        self.assertIn("invalid", out)
        self.assertIn("FAIL (超时/未启动)", out)
        self.assertEqual(_fmt_last({}), "未运行过")
        self.assertEqual(_fmt_last({"last_run": 1, "last_ok": False, "last_rc": 3}),
                         "FAIL (rc=3)")

    def test_cron_run(self):
        with mock.patch("service_cli._api", return_value={"message": "started"}) as api:
            code, out = self._run(cmd_cron_run, "c1")
        self.assertEqual(code, 0)
        self.assertIn("c1 -> started", out)
        self.assertEqual(api.call_args.args[2], "/api/crons/c1/run?via=cli")

    def test_cron_run_requires_name(self):
        with self.assertRaises(SystemExit) as ctx:
            self._run(cmd_cron_run, None)
        self.assertEqual(ctx.exception.code, 2)

    def test_events_with_name_and_count(self):
        payload = {"events": [
            {"ts": "2026-01-01", "svc": None, "act": "host_start", "ok": True, "note": "n"},
            {"ts": "2026-01-02", "svc": "svc-a", "act": "start", "ok": False, "note": "bad"},
        ]}
        with mock.patch("service_cli._api", return_value=payload) as api:
            code, out = self._run(cmd_events, "svc-a", 20)
        self.assertEqual(code, 0)
        self.assertIn("(host)", out)
        self.assertIn("FAIL", out)
        self.assertEqual(api.call_args.args[2], "/api/audit?n=20&svc=svc-a")

    def test_events_numeric_name_is_count(self):
        with mock.patch("service_cli._api", return_value={"events": []}) as api:
            self._run(cmd_events, "30", 50)
        self.assertEqual(api.call_args.args[2], "/api/audit?n=30")

    def test_events_all_skips_filter(self):
        with mock.patch("service_cli._api", return_value={"events": []}) as api:
            self._run(cmd_events, "all", 5)
        self.assertEqual(api.call_args.args[2], "/api/audit?n=5")

    def test_host_err_numeric(self):
        with mock.patch("service_cli._api", return_value={"lines": ["l1", "l2"]}) as api:
            code, out = self._run(cmd_host_err, "10")
        self.assertEqual(code, 0)
        self.assertIn("l1", out)
        self.assertEqual(api.call_args.args[2], "/api/host-err?n=10")

    def test_api_connection_error_exits(self):
        with mock.patch("service_cli.urllib.request.urlopen",
                        side_effect=urllib.error.URLError("down")), \
                self.assertRaises(SystemExit) as ctx:
            _api(self.root, "GET", "/api/services")
        self.assertEqual(ctx.exception.code, 1)

    def test_api_success_parses_json(self):
        class Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b'{"ok": true}'

        with mock.patch("service_cli.urllib.request.urlopen", return_value=Resp()):
            data = _api(self.root, "GET", "/api/services")
        self.assertEqual(data, {"ok": True})


class TestMainDispatch(unittest.TestCase):
    def test_unknown_action_rejected(self):
        with self.assertRaises(SystemExit):
            main(["nope"])

    def test_events_dispatch(self):
        with mock.patch("service_cli._api", return_value={"events": []}), \
                mock.patch("service_cli.resolve_data_root", return_value=Path(".")):
            self.assertEqual(main(["events", "all", "5"]), 0)

    def test_svc_start_dispatch(self):
        with mock.patch("service_cli._api",
                        return_value={"status": "running", "message": "ok"}), \
                mock.patch("service_cli.resolve_data_root", return_value=Path(".")):
            self.assertEqual(main(["svc-start", "a"]), 0)

    def test_svc_start_missing_name(self):
        with mock.patch("service_cli.resolve_data_root", return_value=Path(".")), \
                self.assertRaises(SystemExit) as ctx:
            main(["svc-start"])
        self.assertEqual(ctx.exception.code, 2)


class TestClientHost(unittest.TestCase):
    """客户端地址归一化：通配监听不能当连接目标（Windows 报 WinError 10049）。"""

    def test_wildcard_falls_back_to_loopback(self):
        self.assertEqual(client_host("0.0.0.0"), "127.0.0.1")
        self.assertEqual(client_host("::"), "127.0.0.1")
        self.assertEqual(client_host("[::]"), "127.0.0.1")

    def test_specific_host_kept(self):
        self.assertEqual(client_host("192.168.1.5"), "192.168.1.5")
        self.assertEqual(client_host("127.0.0.1"), "127.0.0.1")

    def test_empty_falls_back(self):
        self.assertEqual(client_host(None), "127.0.0.1")
        self.assertEqual(client_host("  "), "127.0.0.1")

    def test_base_url_uses_loopback_for_wildcard(self):
        root = Path(tempfile.mkdtemp(prefix="ms-cli-host-"))
        try:
            (root / "config.json").write_text(
                '{"host": "0.0.0.0", "port": 4321}', encoding="utf-8")
            self.assertEqual(base_url(root), "http://127.0.0.1:4321")
        finally:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
