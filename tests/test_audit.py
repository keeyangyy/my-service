# -*- coding: utf-8 -*-
"""my-service · tests/test_audit.py — 登记簿清空与自动清理回归

覆盖 audit.clear（按名/全清/坏行保留）与 audit.prune（按天清理/边界）。
audit 模块级 _path 单例：tearDown 还原为 None，避免污染其他测试模块。
"""
from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from core import audit  # noqa: E402


class TestAuditClearAndPrune(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="maudit-test-"))
        audit.init(self.dir)
        audit.emit("cron_run", svc="t-a", dur_ms=10)
        audit.emit("cron_skip", svc="t-a")
        audit.emit("start", svc="your-service")
        audit.emit("host_start", pid=1)

    def tearDown(self):
        audit._path = None          # 还原单例，防止其他模块误写
        import shutil
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_clear_by_svc(self):
        removed = audit.clear("t-a")
        self.assertEqual(removed, 2)
        left = {e["svc"] for e in audit.tail(50)}
        self.assertEqual(left, {"your-service", None})

    def test_clear_all(self):
        removed = audit.clear(None)
        self.assertEqual(removed, 4)
        self.assertEqual(audit.tail(50), [])

    def test_clear_unknown_svc_keeps_all(self):
        removed = audit.clear("ghost")
        self.assertEqual(removed, 0)
        self.assertEqual(len(audit.tail(50)), 4)

    def test_clear_corrupt_line_kept(self):
        with open(audit.current_path(), "a", encoding="utf-8") as fh:
            fh.write("{broken line\n")          # 半行/坏行
        removed = audit.clear("t-a")
        self.assertEqual(removed, 2)
        tail_all = audit.tail(50)
        self.assertEqual(len(tail_all), 2)      # 坏行被 tail 跳过但仍在文件
        self.assertIn("{broken line",
                      audit.current_path().read_text(encoding="utf-8"))

    def test_prune_by_days(self):
        old = datetime.now() - timedelta(days=8)
        with open(audit.current_path(), "a", encoding="utf-8") as fh:
            fh.write(old.strftime('{"ts":"%Y-%m-%d %H:%M:%S.%f",'
                                  '"svc":"old-svc","act":"stop","ok":true}\n'))
        removed = audit.prune(7)
        self.assertEqual(removed, 1)
        left_svcs = {e["svc"] for e in audit.tail(50)}
        self.assertNotIn("old-svc", left_svcs)
        self.assertIn("t-a", left_svcs)

    def test_prune_disabled_and_bad_ts(self):
        with open(audit.current_path(), "a", encoding="utf-8") as fh:
            fh.write('{"ts":"not-a-date","svc":"weird","act":"stop","ok":true}\n')
        self.assertEqual(audit.prune(0), 0)      # 0 = 永不清理
        self.assertEqual(audit.prune(7), 0)      # ts 解析失败保留
        self.assertIn("weird", {e["svc"] for e in audit.tail(50)})


if __name__ == "__main__":
    unittest.main(verbosity=2)
