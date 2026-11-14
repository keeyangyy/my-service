# -*- coding: utf-8 -*-
"""my-service · tests/test_cors.py — 控制面跨源放行 / 外站门禁回归

方案 S：判据只有一条「来源是否本机」。本机网页（任意端口）全放行且能读回结果；
外站网页一律拒绝。不带 Origin 的请求（curl/脚本/同源面板）行为与历史完全一致。

纯标准库 unittest + 真实 HTTP 循环，端口随机，不触碰 reg/ 与 logs/。
运行：python -m unittest tests.test_cors -v   （在 my-service 根目录）
"""
from __future__ import annotations

import json
import sys
import threading
import unittest
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from core import api  # noqa: E402

LOCAL = "http://localhost:8765"
LOCAL_IP = "http://127.0.0.1:8765"
LOCAL_V6 = "http://[::1]:9000"
FOREIGN = "https://example.com"
EVIL = "http://localhost.evil.com:8765"      # 前缀相似，必须判为外站
NULL_ORIGIN = "null"                          # file:// 页面


class _FakeManager:
    """只满足 make_handler 在本用例中用到的接口；start 记录调用参数。"""

    def __init__(self):
        self.calls = []

    def snapshot(self):
        return [{"name": "svc-a", "status": "stopped", "url": LOCAL}]

    def start(self, name, via="api"):
        self.calls.append((name, via))
        return True, "started"

    def stop(self, name):
        self.calls.append((name, "stop"))
        return True, "stopped"

    def restart(self, name, via="api"):
        return True, "restarted"

    def toggle_autostart(self, name, value):
        return True, "ok"

    def set_order(self, names):
        return True, "ok", "ok"

    @property
    def scanner(self):
        class _S:
            reg_dir = "reg"
        return _S()


class _Server:
    """起一个真实 HTTP 服务，端口随机，用完即停。"""

    def __init__(self, cors_local_only: bool = True):
        self.mgr = _FakeManager()
        handler = api.make_handler(self.mgr, None, None, cors_local_only)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def req(self, method, path, origin=None, body=None):
        conn = HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {}
        if origin is not None:
            headers["Origin"] = origin
        payload = None
        if body is not None:
            payload = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        try:
            conn.request(method, path, body=payload, headers=headers)
            resp = conn.getresponse()
            raw = resp.read()
            return resp.status, dict(resp.getheaders()), (
                json.loads(raw.decode("utf-8")) if raw else {})
        finally:
            conn.close()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def _acao(headers):
    """大小写不敏感地取 ACAO 头。"""
    for k, v in headers.items():
        if k.lower() == "access-control-allow-origin":
            return v
    return ""


class TestOriginHelpers(unittest.TestCase):
    """判据本身：本机 / 外站 / 写接口识别。"""

    def test_local_hosts(self):
        for origin in (LOCAL, LOCAL_IP, LOCAL_V6, "http://localhost",
                       "http://127.0.0.1:1", "https://localhost:8443"):
            self.assertTrue(api._is_local_origin(origin), origin)

    def test_foreign_hosts(self):
        # 192.0.2.9 属 RFC 5737 的 TEST-NET-1，专用于文档/测试，避免示例里出现真实私网地址
        for origin in (FOREIGN, EVIL, NULL_ORIGIN, "http://192.0.2.9:8765"):
            self.assertFalse(api._is_local_origin(origin), origin)

    def test_write_paths(self):
        for p in ("/api/services/svc-a/start", "/api/services/svc-a/stop",
                  "/api/services/order", "/api/crons/c/run", "/api/crons/c/config",
                  "/api/crons/order", "/api/audit/clear", "/api/config/svc-a",
                  "/api/host/stop", "/api/host/restart"):
            self.assertTrue(api._is_write_path(p), p)

    def test_read_paths(self):
        for p in ("/api/services", "/api/crons", "/api/host", "/api/audit", "/"):
            self.assertFalse(api._is_write_path(p), p)


class TestCorsHeaders(unittest.TestCase):
    """响应头：本机回显 Origin，外站不给，无 Origin 不加头。"""

    @classmethod
    def setUpClass(cls):
        cls.srv = _Server()

    @classmethod
    def tearDownClass(cls):
        cls.srv.close()

    def test_local_origin_gets_acao(self):
        code, headers, body = self.srv.req("GET", "/api/services", origin=LOCAL)
        self.assertEqual(code, 200)
        self.assertEqual(_acao(headers), LOCAL)
        self.assertIn("services", body)

    def test_local_ip_origin_gets_acao(self):
        _, headers, _ = self.srv.req("GET", "/api/services", origin=LOCAL_IP)
        self.assertEqual(_acao(headers), LOCAL_IP)

    def test_no_origin_no_header(self):
        """curl / 脚本 / 同源面板：行为与历史完全一致。"""
        _, headers, _ = self.srv.req("GET", "/api/services")
        self.assertEqual(_acao(headers), "")

    def test_foreign_origin_no_acao(self):
        """外站读接口也拿不到介绍信，浏览器自会拦下响应。"""
        _, headers, _ = self.srv.req("GET", "/api/services", origin=FOREIGN)
        self.assertEqual(_acao(headers), "")


class TestPreflight(unittest.TestCase):
    """预检 OPTIONS：本机 204，外站 403。"""

    @classmethod
    def setUpClass(cls):
        cls.srv = _Server()

    @classmethod
    def tearDownClass(cls):
        cls.srv.close()

    def test_local_preflight(self):
        code, headers, _ = self.srv.req("OPTIONS", "/api/services/svc-a/start",
                                        origin=LOCAL)
        self.assertEqual(code, 204)
        self.assertEqual(_acao(headers), LOCAL)
        self.assertIn("POST", headers.get("Access-Control-Allow-Methods", ""))
        self.assertIn("Content-Type", headers.get("Access-Control-Allow-Headers", ""))
        self.assertEqual(headers.get("Access-Control-Max-Age"), "600")

    def test_no_origin_preflight(self):
        """无 Origin 的预检：放行但不发 CORS 头（与普通请求一致）。"""
        code, headers, _ = self.srv.req("OPTIONS", "/api/services/svc-a/start")
        self.assertEqual(code, 204)
        self.assertEqual(_acao(headers), "")

    def test_foreign_preflight_denied(self):
        code, headers, body = self.srv.req("OPTIONS", "/api/services/svc-a/start",
                                           origin=FOREIGN)
        self.assertEqual(code, 403)
        self.assertEqual(_acao(headers), "")
        self.assertFalse(body.get("ok"))

    def test_file_origin_denied(self):
        """file:// 页面的 Origin 是字符串 null，按外站处理。"""
        code, _, _ = self.srv.req("OPTIONS", "/api/services/svc-a/start",
                                  origin=NULL_ORIGIN)
        self.assertEqual(code, 403)


class TestForeignWriteGate(unittest.TestCase):
    """外站写接口必被拒；本机与无 Origin 照常执行。"""

    def setUp(self):
        self.srv = _Server()

    def tearDown(self):
        self.srv.close()

    def test_local_write_allowed(self):
        code, headers, body = self.srv.req("POST", "/api/services/svc-a/start",
                                           origin=LOCAL)
        self.assertEqual(code, 202, body)
        self.assertEqual(_acao(headers), LOCAL)
        self.assertEqual(self.srv.mgr.calls, [("svc-a", "api")])

    def test_no_origin_write_allowed(self):
        code, _, _ = self.srv.req("POST", "/api/services/svc-a/start")
        self.assertEqual(code, 202)
        self.assertEqual(self.srv.mgr.calls, [("svc-a", "api")])

    def test_foreign_write_denied(self):
        for path in ("/api/services/svc-a/start", "/api/services/svc-a/stop",
                     "/api/services/order", "/api/host/stop", "/api/audit/clear",
                     "/api/config/svc-a"):
            code, _, body = self.srv.req("POST", path, origin=FOREIGN,
                                         body={"anything": True})
            self.assertEqual(code, 403, f"{path} 应被拒，实际 {code}")
            self.assertEqual(body.get("error"), "cross-origin denied")
        self.assertEqual(self.srv.mgr.calls, [])   # 一个都没真执行

    def test_similar_host_denied(self):
        """localhost.evil.com 不能借前缀蒙混过关。"""
        code, _, _ = self.srv.req("POST", "/api/services/svc-a/start", origin=EVIL,
                                  body={})
        self.assertEqual(code, 403)

    def test_foreign_read_still_served(self):
        """外站读接口仍返回内容（无 ACAO），浏览器负责拦——服务端不替浏览器决策。"""
        code, headers, body = self.srv.req("GET", "/api/services", origin=FOREIGN)
        self.assertEqual(code, 200)
        self.assertEqual(_acao(headers), "")
        self.assertIn("services", body)


class TestSwitchOff(unittest.TestCase):
    """cors_local_only=False：完全回到历史语义。"""

    def setUp(self):
        self.srv = _Server(cors_local_only=False)

    def tearDown(self):
        self.srv.close()

    def test_no_acao_header(self):
        _, headers, _ = self.srv.req("GET", "/api/services", origin=LOCAL)
        self.assertEqual(_acao(headers), "")

    def test_foreign_write_not_denied(self):
        """关掉开关后不再拦截（仅应急用，正常情况下保持开启）。"""
        code, _, _ = self.srv.req("POST", "/api/services/svc-a/start", origin=FOREIGN,
                                  body={})
        self.assertNotEqual(code, 403)


if __name__ == "__main__":
    unittest.main(verbosity=2)