# -*- coding: utf-8 -*-
"""my-service · tests/test_backup.py — 实例配置备份

覆盖：备份 reg/ + config.json + adapters/、同秒冲突加后缀、列表倒序与大小。
纯标准库 + tempfile 隔离。
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

from core import backup  # noqa: E402


class TestBackup(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="msbak-test-"))
        (self.root / "reg" / "services").mkdir(parents=True)
        (self.root / "reg" / "tasks").mkdir(parents=True)
        (self.root / "reg" / "services" / "a.json").write_text(
            json.dumps({"path": str(self.root)}), encoding="utf-8")
        (self.root / "reg" / "tasks" / "t.json").write_text(
            json.dumps({"schedule": {"type": "daily", "at": "09:30"}}),
            encoding="utf-8")
        (self.root / "config.json").write_text(
            json.dumps({"host": "127.0.0.1"}), encoding="utf-8")
        (self.root / "adapters" / "svc").mkdir(parents=True)
        (self.root / "adapters" / "svc" / "start.ps1").write_text(
            "# noop\n", encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_create_includes_reg_config_adapters(self):
        info = backup.create_backup(self.root)
        dest = Path(info["path"])
        self.assertTrue(dest.is_dir())
        self.assertEqual(info["files"], 4)
        self.assertTrue((dest / "reg" / "services" / "a.json").is_file())
        self.assertTrue((dest / "reg" / "tasks" / "t.json").is_file())
        self.assertTrue((dest / "config.json").is_file())
        self.assertTrue((dest / "adapters" / "svc" / "start.ps1").is_file())

    def test_same_second_gets_suffix(self):
        first = backup.create_backup(self.root)
        second = backup.create_backup(self.root)
        self.assertNotEqual(first["name"], second["name"])
        self.assertTrue(Path(second["path"]).is_dir())

    def test_list_sorted_desc(self):
        backup.create_backup(self.root)
        backup.create_backup(self.root)
        items = backup.list_backups(self.root)
        self.assertEqual(len(items), 2)
        self.assertGreaterEqual(items[0]["name"], items[1]["name"])
        self.assertGreater(items[0]["size"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
