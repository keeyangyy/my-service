"""API 冒烟测试：进程内起 HTTP 服务，验证四个只读接口。

覆盖外部最常调用的入口：`/health`、`/api/services`、`/api/settings`、`/api/backups`。
只验证「装配 → 路由 → JSON 形状」这条链路，不启动 monitor / scheduler 线程。
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from core import api  # noqa: E402
from core.scanner import Scanner  # noqa: E402
from core.scheduler import Scheduler  # noqa: E402
from core.state import Manager  # noqa: E402


class TestApiSmoke(unittest.TestCase):
    """一个实例跑完四个接口：省去重复起停，失败时断言仍能定位。"""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="ms-smoke-"))
        reg = self.root / "reg"
        (reg / "services").mkdir(parents=True)
        (reg / "tasks").mkdir(parents=True)
        self.cfg = self.root / "config.json"
        self.cfg.write_text(
            json.dumps({"host": "127.0.0.1", "port": 0, "reg_dir": "reg"}),
            encoding="utf-8",
        )
        manager = Manager(
            Scanner(reg / "services"),
            settings={"fast_sec": 1.0, "idle_sec": 10.0,
                      "panel_grace_sec": 15.0, "audit_retention_days": 7},
        )
        scheduler = Scheduler(reg / "tasks", self.root / "cron_state.json", tick_sec=1.0)
        self.httpd = api.serve(manager, "127.0.0.1", 0,
                               scheduler=scheduler, settings_path=self.cfg)
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)
        shutil.rmtree(self.root, ignore_errors=True)

    def _get(self, path: str) -> tuple[int, dict]:
        with urllib.request.urlopen(self.base + path, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))

    def test_health(self):
        status, body = self._get("/health")
        self.assertEqual(status, 200)
        self.assertIs(body["ok"], True)

    def test_services_empty(self):
        status, body = self._get("/api/services")
        self.assertEqual(status, 200)
        self.assertEqual(body["services"], [])

    def test_settings_shape(self):
        status, body = self._get("/api/settings")
        self.assertEqual(status, 200)
        self.assertIn("monitor", body["advanced"])
        self.assertIn("scheduler", body["advanced"])
        self.assertEqual(body["backup"]["keep"], 7)

    def test_backups_empty(self):
        status, body = self._get("/api/backups")
        self.assertEqual(status, 200)
        self.assertEqual(body["backups"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
