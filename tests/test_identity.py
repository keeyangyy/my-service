"""my-service · tests/test_identity.py — 实例身份判定回归

覆盖 Manager.identify() 的四态判定链与配套安全约束：

  mine     本服务实例（可用性：可接管、可回收、可停）
  foreign  端口被别的程序占（框架无权干涉，绝不冒充、绝不误停）
  unknown  有应答但无身份凭证（保守呈现，不冒充成功）
  absent   端口无监听 / 无凭证（未运行）

以及：
  - reconcile() 启动接管（宿主重启后的孤儿收敛）
  - _reap_idle() 只在身份确认为 mine 时回收
  - stop() 对 conflict 的"清除告警"不得打到外人服务上（安全闸）

用真实 socket 起桩服务，不 mock 网络层——身份判定的价值恰恰在真实响应上。
纯标准库，tempfile 隔离。
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import threading
import unittest
from unittest import mock
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from core import audit, runner                         # noqa: E402
from core.scanner import Scanner                       # noqa: E402
from core.state import Manager                         # noqa: E402


class _StubHandler(BaseHTTPRequestHandler):
    """按类变量 service_field 决定 /health 自报内容。"""

    service_field: object = None       # None=不带该字段；str=自报该值

    def do_GET(self):  # noqa: N802
        body: dict = {"ok": True}
        if self.__class__.service_field is not None:
            body["service"] = self.__class__.service_field
        raw = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):  # noqa: N802
        # 任何人调用此桩的 POST 都记账——用于证明"框架没误伤外人"
        self.__class__.post_hits += 1
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *a):  # 静音
        pass


def _stub(port: int, service_field: object):
    """起一个桩 HTTP 服务，返回 (server, thread, handler_cls)。"""
    cls = type("Stub", (_StubHandler,), {"service_field": service_field,
                                         "post_hits": 0})
    srv = HTTPServer(("127.0.0.1", port), cls)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    return srv, th, cls


class TestIdentify(unittest.TestCase):
    """identify() 四态判定。"""

    PORT = 8793

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="msident-test-"))
        self.reg = self.root / "reg"
        self.reg.mkdir()
        self.home = self.root / "svc"
        self.home.mkdir()
        (self.home / "start.ps1").write_text("# noop\n", encoding="utf-8")
        self._write_reg()
        runner.set_runtime_dir(self.root / "data")
        audit.init(self.root / "logs")
        self.srv = None
        self.mgr = Manager(Scanner(self.reg), self.root / "order.json")

    def tearDown(self):
        if self.srv is not None:
            self.srv.shutdown()
            self.srv.server_close()
        audit._path = None
        runner.set_runtime_dir(Path(tempfile.gettempdir()) / "msident-gone")
        shutil.rmtree(self.root, ignore_errors=True)

    def _write_reg(self, extra: dict | None = None):
        reg = {
            "path": str(self.home), "mode": "on_demand",
            "start": {"entry": "start.ps1"},
            "stop": {"script": "stop.ps1"},
            "health": {"type": "http",
                       "url": f"http://127.0.0.1:{self.PORT}/health"},
        }
        if extra:
            reg.update(extra)
        (self.reg / "svc.json").write_text(json.dumps(reg), encoding="utf-8")

    def _reload(self):
        self.mgr = Manager(Scanner(self.reg), self.root / "order.json")

    # ---------------------------------------------------------- absent
    def test_absent_when_port_closed(self):
        v, via, why = self.mgr.identify("svc")
        self.assertEqual(v, "absent", why)

    def test_absent_does_not_spawn_subprocess(self):
        """absent 路径必须廉价：socket 预筛，不走 urlopen 的 ~2s 重试路径。"""
        import time
        t0 = time.time()
        for _ in range(3):
            self.mgr.identify("svc")
        self.assertLess(time.time() - t0, 1.0,
                        "端口关闭时 identify 过慢（疑似走了 urlopen 重试）")

    # ---------------------------------------------------------- mine
    def test_mine_when_service_field_matches(self):
        self.srv, _, _ = _stub(self.PORT, "svc")
        v, via, why = self.mgr.identify("svc")
        self.assertEqual(v, "mine", why)
        self.assertEqual(via, "health-service")

    def test_mine_honours_identity_service_override(self):
        """注册条可用 identity.service 覆盖身份名（自写服务改过 service 值）。"""
        self._write_reg({"identity": {"service": "another-name"}})
        self._reload()
        self.srv, _, _ = _stub(self.PORT, "another-name")
        v, _, why = self.mgr.identify("svc")
        self.assertEqual(v, "mine", why)

    def test_mine_via_own_pidfile(self):
        """框架 pid 凭证存活 **且端口在监听** → mine。"""
        self.srv, _, _ = _stub(self.PORT, None)   # 端口有监听（身份另由凭证给）
        (runner.pidfile_path(self.mgr.get_spec("svc"))).write_text(
            str(__import__("os").getpid()), encoding="ascii")
        v, via, _ = self.mgr.identify("svc")
        self.assertEqual(v, "mine")
        self.assertEqual(via, "own-pidfile")

    def test_stale_pidfile_alone_is_not_mine(self):
        """回归：凭证 pid 存活但端口无监听 → 绝不判 mine。

        这是"旧进程残留凭证"的典型风险：pid 会被系统回收给别的进程，
        于是残留凭证里的 pid"存活"，但它根本不是本服务。若只看 pid 存活
        （不看端口），框架会把别人的进程认成自家实例，进而接管/回收它。
        """
        import os
        pf = runner.pidfile_path(self.mgr.get_spec("svc"))
        pf.write_text(str(os.getpid()), encoding="ascii")   # 本测试进程，确实存活
        # 注意：本用例不起桩服务，端口无监听
        v, via, why = self.mgr.identify("svc")
        self.assertEqual(v, "absent", why)
        self.assertEqual(via, "stale-pidfile")

    # ---------------------------------------------------------- foreign
    def test_foreign_when_service_field_differs(self):
        self.srv, _, _ = _stub(self.PORT, "someone-else")
        v, via, why = self.mgr.identify("svc")
        self.assertEqual(v, "foreign", why)
        self.assertEqual(via, "health-service")

    # ---------------------------------------------------------- unknown
    def test_unknown_when_no_service_field(self):
        """有应答但响应体无 service 字段、注册条也无 pidfile → unknown（不冒充）。"""
        self.srv, _, _ = _stub(self.PORT, None)
        v, _, why = self.mgr.identify("svc")
        self.assertEqual(v, "unknown", why)

    def test_no_service_field_but_declared_pidfile_alive_is_mine(self):
        """无 service 字段时，注册条声明的 pidfile 是兜底凭证（商业软件场景）。"""
        import os
        pf = self.root / "ext.pid"
        pf.write_text(str(os.getpid()), encoding="ascii")
        self._write_reg({"identity": {"pidfile": str(pf)}})
        self._reload()
        self.srv, _, _ = _stub(self.PORT, None)
        v, via, why = self.mgr.identify("svc")
        self.assertEqual(v, "mine", why)
        self.assertEqual(via, "declared-pidfile")


class TestStopSafetyGate(unittest.TestCase):
    """conflict 下点"清除"绝不能打到外人服务上。"""

    PORT = 8794

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="msgate-test-"))
        self.reg = self.root / "reg"
        self.reg.mkdir()
        self.home = self.root / "svc"
        self.home.mkdir()
        (self.home / "start.ps1").write_text("# noop\n", encoding="utf-8")
        (self.home / "stop.ps1").write_text(
            # 这个脚本若被调用，就写下铁证——安全闸本该拦住它
            f"Set-Content -Path '{self.root / 'LEAKED.txt'}' -Value 'hit'\n",
            encoding="utf-8")
        (self.reg / "svc.json").write_text(json.dumps({
            "path": str(self.home), "mode": "on_demand",
            "start": {"entry": "start.ps1"},
            "stop": {"script": "stop.ps1"},
            "health": {"type": "http",
                       "url": f"http://127.0.0.1:{self.PORT}/health"},
        }), encoding="utf-8")
        runner.set_runtime_dir(self.root / "data")
        audit.init(self.root / "logs")
        self.srv, _, self.cls = _stub(self.PORT, "impostor")
        self.mgr = Manager(Scanner(self.reg), self.root / "order.json")

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        audit._path = None
        runner.set_runtime_dir(Path(tempfile.gettempdir()) / "msgate-gone")
        shutil.rmtree(self.root, ignore_errors=True)

    def test_stop_on_conflict_does_not_touch_foreign(self):
        self.mgr.state["svc"].update(status="stopped", error="端口被占", port_foreign=True)
        ok, msg = self.mgr.stop("svc")
        self.assertTrue(ok, msg)
        self.assertEqual(self.cls.post_hits, 0, "框架把请求打到了外人服务上！")
        self.assertFalse((self.root / "LEAKED.txt").exists(),
                         "端口被外人占时仍执行了 stop_script——安全闸失效")
        self.assertEqual(self.mgr.state["svc"]["status"], "stopped")

    def test_start_on_conflict_marks_stopped_with_error(self):
        """端口被外人占时点启动：状态 stopped + 错误说明（不重复拉起）。"""
        ok, msg = self.mgr.start("svc")
        self.assertFalse(ok)
        self.assertEqual(self.mgr.state["svc"]["status"], "stopped", msg)
        self.assertTrue(self.mgr.state["svc"].get("error"))


class TestReconcile(unittest.TestCase):
    """宿主启动接管：孤儿认得出、外人标 conflict、absent 清过期凭证。"""

    PORT = 8795

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="msrecon-test-"))
        self.reg = self.root / "reg"
        self.reg.mkdir()
        self.home = self.root / "svc"
        self.home.mkdir()
        (self.home / "start.ps1").write_text("# noop\n", encoding="utf-8")
        (self.reg / "svc.json").write_text(json.dumps({
            "path": str(self.home), "mode": "on_demand",
            "start": {"entry": "start.ps1"},
            "stop": {"script": "stop.ps1"},
            "health": {"type": "http",
                       "url": f"http://127.0.0.1:{self.PORT}/health"},
        }), encoding="utf-8")
        runner.set_runtime_dir(self.root / "data")
        audit.init(self.root / "logs")
        self.srv = None

    def tearDown(self):
        if self.srv is not None:
            self.srv.shutdown()
            self.srv.server_close()
        audit._path = None
        runner.set_runtime_dir(Path(tempfile.gettempdir()) / "msrecon-gone")
        shutil.rmtree(self.root, ignore_errors=True)

    def test_reconcile_adopts_own_orphan(self):
        self.srv, _, _ = _stub(self.PORT, "svc")
        mgr = Manager(Scanner(self.reg), self.root / "order.json")
        out = mgr.reconcile()
        self.assertEqual(mgr.state["svc"]["status"], "running", str(out))
        self.assertIn("归位", out.get("svc", ""))

    def test_reconcile_marks_foreign_as_stopped(self):
        self.srv, _, _ = _stub(self.PORT, "impostor")
        mgr = Manager(Scanner(self.reg), self.root / "order.json")
        out = mgr.reconcile()
        self.assertEqual(mgr.state["svc"]["status"], "stopped", str(out))
        self.assertIn("他人", out.get("svc", ""))

    def test_reconcile_clears_stale_pidfile_when_absent(self):
        mgr = Manager(Scanner(self.reg), self.root / "order.json")
        pf = runner.pidfile_path(mgr.get_spec("svc"))
        pf.write_text("999999", encoding="ascii")     # 不存在的 pid
        out = mgr.reconcile()
        self.assertFalse(pf.exists(), "absent 时应清掉过期 pid 凭证")
        self.assertEqual(out.get("svc"), "未运行")


class TestPortHealthWithProcessIdentity(unittest.TestCase):
    """port 型 + identity.process：唯一匹配且与端口属主一致才算 mine。

    这是 v0.15.1 补的能力：此前 port 型只有 pidfile 一条路，第三方二进制
    （sd-server.exe 之类）没有 pidfile 就永远判 unknown。
    """

    PORT = 8791

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="msident-port-"))
        self.reg = self.root / "reg"
        self.reg.mkdir()
        self.home = self.root / "svc"
        self.home.mkdir()
        (self.home / "start.ps1").write_text("# noop\n", encoding="utf-8")
        runner.set_runtime_dir(self.root / "data")
        audit.init(self.root / "logs")
        self.srv = None

    def tearDown(self):
        if self.srv is not None:
            self.srv.shutdown()
            self.srv.server_close()
        audit._path = None
        runner.set_runtime_dir(Path(tempfile.gettempdir()) / "msident-gone")
        shutil.rmtree(self.root, ignore_errors=True)

    def _write_port_reg(self, identity=None):
        reg = {
            "path": str(self.home), "mode": "managed",
            "start": {"entry": "start.ps1"},
            "health": {"type": "port", "port": self.PORT},
        }
        if identity:
            reg["identity"] = identity
        (self.reg / "svc.json").write_text(json.dumps(reg), encoding="utf-8")
        self.mgr = Manager(Scanner(self.reg), self.root / "order.json")

    def test_mine_when_process_matches_port_owner(self):
        self._write_port_reg({"process": "svc.exe", "match": "name"})
        self.srv, _, _ = _stub(self.PORT, None)
        with mock.patch.object(runner, "find_processes", return_value=[4321]), \
                mock.patch.object(runner, "port_owner_pid", return_value=4321):
            v, via, why = self.mgr.identify("svc")
        self.assertEqual(v, "mine", why)
        self.assertEqual(via, "process")

    def test_foreign_when_process_is_not_port_owner(self):
        self._write_port_reg({"process": "svc.exe", "match": "name"})
        self.srv, _, _ = _stub(self.PORT, None)
        with mock.patch.object(runner, "find_processes", return_value=[4321]), \
                mock.patch.object(runner, "port_owner_pid", return_value=9999):
            v, via, why = self.mgr.identify("svc")
        self.assertEqual(v, "foreign", why)
        self.assertEqual(via, "process-port-mismatch")

    def test_unknown_without_identity_process(self):
        """port 型且没有 identity：保守判 unknown，不冒充运行中。"""
        self._write_port_reg()
        self.srv, _, _ = _stub(self.PORT, None)
        v, _, _ = self.mgr.identify("svc")
        self.assertEqual(v, "unknown")


if __name__ == "__main__":
    unittest.main(verbosity=2)
