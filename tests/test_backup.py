"""my-service · tests/test_backup.py — 实例配置备份

覆盖：备份 reg/ + config.json、同秒冲突加后缀、列表倒序与大小。
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

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_create_includes_reg_and_config(self):
        info = backup.create_backup(self.root)
        dest = Path(info["path"])
        self.assertTrue(dest.is_dir())
        self.assertEqual(info["files"], 3)
        self.assertTrue((dest / "reg" / "services" / "a.json").is_file())
        self.assertTrue((dest / "reg" / "tasks" / "t.json").is_file())
        self.assertTrue((dest / "config.json").is_file())
        self.assertFalse((dest / "adapters").exists())   # 兼容层已移除

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

    def test_prune_keeps_latest(self):
        for name in ("20260101-000000", "20260102-000000", "20260103-000000"):
            (self.root / "data" / "bak" / name).mkdir(parents=True)
        removed = backup.prune_backups(self.root, keep=2)
        self.assertEqual(removed, ["20260101-000000"])
        self.assertEqual([p.name for p in backup._backup_dirs(self.root)],
                         ["20260103-000000", "20260102-000000"])

    def test_prune_zero_keeps_all(self):
        for name in ("20260101-000000", "20260102-000000"):
            (self.root / "data" / "bak" / name).mkdir(parents=True)
        self.assertEqual(backup.prune_backups(self.root, keep=0), [])
        self.assertEqual(len(backup._backup_dirs(self.root)), 2)

    def test_create_backup_prunes_over_limit(self):
        first = backup.create_backup(self.root, keep=1)
        second = backup.create_backup(self.root, keep=1)
        self.assertEqual(second["pruned"], [first["name"]])
        names = [p.name for p in backup._backup_dirs(self.root)]
        self.assertEqual(names, [second["name"]])


if __name__ == "__main__":
    unittest.main(verbosity=2)
