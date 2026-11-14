# -*- coding: utf-8 -*-
"""my-service · tests/test_process_info.py — 进程定位底层（Windows）

覆盖：进程创建时间、端口→PID、进程名枚举、locate 的回退顺序。
用当前测试进程与本地临时 HTTP 服务做真实验证，不 mock Win32。
"""
from __future__ import annotations

import os
import socket
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from core import runner  # noqa: E402


class _Quiet(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        body = b'{"ok": true}'
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class TestProcessInfo(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "只验证 Windows 实现")
    def test_create_time_for_self(self):
        t = runner.process_create_time(os.getpid())
        self.assertIsNotNone(t)
        self.assertLessEqual(t, time.time() + 5)
        self.assertGreater(t, time.time() - 86400 * 30)

    @unittest.skipUnless(os.name == "nt", "只验证 Windows 实现")
    def test_image_path_for_self(self):
        p = runner.process_image_path(os.getpid())
        self.assertIsNotNone(p)
        self.assertTrue(Path(p).exists(), p)

    @unittest.skipUnless(os.name == "nt", "只验证 Windows 实现")
    def test_port_owner_pid(self):
        port = _free_port()
        srv = HTTPServer(("127.0.0.1", port), _Quiet)
        th = threading.Thread(target=srv.serve_forever, daemon=True)
        th.start()
        try:
            deadline = time.time() + 3
            got = None
            while time.time() < deadline:
                got = runner.port_owner_pid(port)
                if got:
                    break
                time.sleep(0.05)
            self.assertEqual(got, os.getpid())
        finally:
            srv.shutdown()
            srv.server_close()

    @unittest.skipUnless(os.name == "nt", "只验证 Windows 实现")
    def test_find_processes_by_name(self):
        exe = Path(sys.executable).name
        pids = runner.find_processes(exe, "name")
        self.assertIn(os.getpid(), pids)

    @unittest.skipUnless(os.name == "nt", "只验证 Windows 实现")
    def test_locate_prefers_handle(self):
        class _FakePopen:
            pid = os.getpid()

            @staticmethod
            def poll():
                return None

        class _FakeHandle:
            popen = _FakePopen()

            @property
            def pid(self):
                return os.getpid()

        spec = _spec(port=None)
        pid, source, confidence = runner.locate(spec, _FakeHandle())
        self.assertEqual(pid, os.getpid())
        self.assertEqual(source, "handle")
        self.assertEqual(confidence, "confirmed")

    @unittest.skipUnless(os.name == "nt", "只验证 Windows 实现")
    def test_locate_via_port_without_handle(self):
        port = _free_port()
        srv = HTTPServer(("127.0.0.1", port), _Quiet)
        th = threading.Thread(target=srv.serve_forever, daemon=True)
        th.start()
        try:
            spec = _spec(port=port)
            pid, source, confidence = runner.locate(spec, None)
            self.assertEqual(pid, os.getpid())
            self.assertEqual(source, "port")
            self.assertEqual(confidence, "probable")
        finally:
            srv.shutdown()
            srv.server_close()


def _spec(port: int | None):
    """最小 Spec 替身：locate 只用到这几个属性。"""

    class _Spec:
        folder = "test"
        health_type = "port" if port else "process"
        health_port = port
        health_url = None
        pidfile = None
        identity_process = None
        identity_match = "name"
        path = Path(tempfile_dir())

    return _Spec()


def tempfile_dir() -> str:
    import tempfile
    return tempfile.gettempdir()


if __name__ == "__main__":
    unittest.main(verbosity=2)
