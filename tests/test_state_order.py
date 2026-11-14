# -*- coding: utf-8 -*-
"""my-service · tests/test_state_order.py — 服务列表拖拽排序回归

覆盖 Manager 的顺序账本：默认字母序、重排、未知名忽略、部分列表追加、
跨重启保持、注销服务自动清理顺序条目。纯标准库，tempfile 隔离。
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from core.scanner import Scanner  # noqa: E402
from core.state import Manager    # noqa: E402


class TestServiceOrdering(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="msorder-test-"))
        self.reg = self.root / "reg"
        self.reg.mkdir()
        for name in ("a-svc", "b-svc", "c-svc"):
            home = self.root / name
            home.mkdir()
            (home / "start.ps1").write_text("# noop\n", encoding="utf-8")
            (self.reg / f"{name}.json").write_text(json.dumps(
                {"path": str(home), "mode": "managed",
                 "start": {"entry": "start.ps1"},
                 "health": {"type": "port", "port": 8790}}), encoding="utf-8")
        self.mgr = self._mgr()

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _mgr(self) -> Manager:
        return Manager(Scanner(self.reg), self.root / "service_order.json")

    def _names(self) -> list[str]:
        return [s["name"] for s in self.mgr.snapshot()]

    def test_default_is_alphabetical(self):
        self.assertEqual(self._names(), ["a-svc", "b-svc", "c-svc"])

    def test_set_order_reorders(self):
        ok, code, _ = self.mgr.set_order(["c-svc", "a-svc", "b-svc"])
        self.assertTrue(ok)
        self.assertEqual(self._names(), ["c-svc", "a-svc", "b-svc"])

    def test_unknown_ignored_and_deduped(self):
        self.mgr.set_order(["ghost", "b-svc", "b-svc"])
        self.assertEqual(self._names(), ["b-svc", "a-svc", "c-svc"])

    def test_partial_list_appends_rest(self):
        self.mgr.set_order(["c-svc"])
        self.assertEqual(self._names(), ["c-svc", "a-svc", "b-svc"])

    def test_order_survives_restart(self):
        self.mgr.set_order(["b-svc", "c-svc", "a-svc"])
        m2 = self._mgr()
        self.assertEqual([s["name"] for s in m2.snapshot()],
                         ["b-svc", "c-svc", "a-svc"])

    def test_new_service_appended_not_disrupting(self):
        self.mgr.set_order(["c-svc", "b-svc", "a-svc"])
        home = self.root / "d-svc"
        home.mkdir()
        (home / "start.ps1").write_text("# noop\n", encoding="utf-8")
        (self.reg / "d-svc.json").write_text(json.dumps(
            {"path": str(home), "mode": "managed",
             "start": {"entry": "start.ps1"},
             "health": {"type": "port", "port": 8790}}), encoding="utf-8")
        self.mgr.refresh()
        self.assertEqual(self._names(), ["c-svc", "b-svc", "a-svc", "d-svc"])

    def test_removed_service_cleaned_from_ledger(self):
        self.mgr.set_order(["c-svc", "b-svc", "a-svc"])
        (self.reg / "b-svc.json").unlink()
        self.mgr.refresh()                       # monitor 每秒都会做，这里手动触发
        self.assertEqual(self.mgr._order, ["c-svc", "a-svc"])
        self.assertEqual(self._names(), ["c-svc", "a-svc"])
        saved = {
            n: json.loads((self.reg / f"{n}.json").read_text(encoding="utf-8"))
            .get("runtime", {}).get("order")
            for n in ("c-svc", "a-svc")
        }
        self.assertEqual(saved, {"c-svc": 1, "a-svc": 2})

    def test_no_order_path_falls_back_to_alphabetical(self):
        m = Manager(Scanner(self.reg))            # 未给 order 路径（脚本/单测直调）
        self.assertEqual([s["name"] for s in m.snapshot()],
                         ["a-svc", "b-svc", "c-svc"])
        m.set_order(["c-svc", "a-svc", "b-svc"])  # 内存生效，只是不落盘
        self.assertEqual([s["name"] for s in m.snapshot()],
                         ["c-svc", "a-svc", "b-svc"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
