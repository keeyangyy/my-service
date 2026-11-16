"""my-service · tests/test_service_config.py — 服务配置行内编辑

覆盖 Manager.update_config 的 idle_timeout_sec：保存、边界拒绝、非 on_demand 拒绝、
未知服务、坏值回滚不留脏注册条；以及自启开关写回注册条 runtime。

纯标准库 + tempfile 隔离，不触碰真实 reg/ 与 logs/，绝不启动任何进程。
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
from core.state import Manager  # noqa: E402


class TestServiceUpdateConfig(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="mscfg-test-"))
        self.reg = self.root / "reg"
        self.reg.mkdir()
        self.home = self.root / "svc-od"
        self.home.mkdir()
        (self.home / "server.py").write_text("print(1)\n", encoding="utf-8")
        self._write("svc-od", path=str(self.home), mode="on_demand",
                    start={"entry": "server.py"},
                    stop={"script": "stop.ps1"},
                    identity={"http": "http://127.0.0.1:8799/health"},
                    settings={"idle_timeout_sec": 600, "autostart": False})
        self.home2 = self.root / "svc-det"
        self.home2.mkdir()
        (self.home2 / "start.ps1").write_text("# noop\n", encoding="utf-8")
        (self.home2 / "stop.ps1").write_text("# noop\n", encoding="utf-8")
        # 点火脚本：managed + start.detach，必须有 stop 脚本
        self._write("svc-det", path=str(self.home2), mode="managed",
                    start={"entry": "start.ps1", "detach": True},
                    stop={"script": "stop.ps1"},
                    identity={"port": 8798},
                    settings={"autostart": False})
        self.mgr = Manager(Scanner(self.reg), self.root / "order.json")
        self.mgr.refresh()
        # 装置自检：注册条必须真的解析成功，否则整组测试会在错误前提上"通过"
        for nm in ("svc-od", "svc-det"):
            sp = self.mgr.get_spec(nm)
            self.assertIsNotNone(sp, f"{nm} 未被扫描到，fixture 有误")
            self.assertTrue(sp.valid, f"{nm} 注册条无效: {sp.error}")
        self.assertEqual(self.mgr.get_spec("svc-od").mode, "on_demand")

    def _write(self, name, **kw):
        (self.reg / f"{name}.json").write_text(
            json.dumps(kw, ensure_ascii=False), encoding="utf-8")

    def _raw(self, name):
        return json.loads((self.reg / f"{name}.json").read_text(encoding="utf-8"))

    def _rt(self, name):
        """注册条 settings 组；无记录即空。"""
        try:
            data = json.loads((self.reg / f"{name}.json").read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            data = {}
        return data.get("settings") or {}

    def _reg_bytes(self, name):
        return (self.reg / f"{name}.json").read_bytes()

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _snap(self, name):
        return next(s for s in self.mgr.snapshot() if s["name"] == name)

    # ---- 成功路径 ----
    def test_save_idle(self):
        """改空闲回收时长：写回注册条 runtime 组。"""
        ok, code, msg = self.mgr.update_config("svc-od", {"idle_timeout_sec": 900})
        self.assertTrue(ok, msg)
        self.assertEqual(self._rt("svc-od")["idle_timeout_sec"], 900)
        self.assertEqual(self._snap("svc-od")["idle_timeout"], 900.0)

    def test_bounds_accepted(self):
        for v in (60, 86400):
            ok, _, msg = self.mgr.update_config("svc-od", {"idle_timeout_sec": v})
            self.assertTrue(ok, f"{v}: {msg}")

    # ---- 拒绝路径：必须比 Spec 的 clamp 更严，否则文件与内存不一致 ----
    def test_below_min_rejected(self):
        ok, code, msg = self.mgr.update_config("svc-od", {"idle_timeout_sec": 5})
        self.assertFalse(ok)
        self.assertEqual(code, "invalid")
        self.assertIn("60", msg)
        self.assertEqual(self._rt("svc-od")["idle_timeout_sec"], 600,
                         "越界值不得写回注册条")

    def test_above_max_rejected(self):
        ok, code, _ = self.mgr.update_config("svc-od", {"idle_timeout_sec": 999999})
        self.assertFalse(ok)
        self.assertEqual(code, "invalid")
        self.assertEqual(self._rt("svc-od")["idle_timeout_sec"], 600)

    def test_non_numeric_rejected(self):
        ok, code, _ = self.mgr.update_config("svc-od", {"idle_timeout_sec": "abc"})
        self.assertFalse(ok)
        self.assertEqual(code, "invalid")

    def test_empty_patch_rejected(self):
        ok, code, _ = self.mgr.update_config("svc-od", {})
        self.assertFalse(ok)
        self.assertEqual(code, "invalid")

    def test_non_on_demand_rejected(self):
        ok, code, msg = self.mgr.update_config("svc-det", {"idle_timeout_sec": 300})
        self.assertFalse(ok)
        self.assertEqual(code, "invalid")
        self.assertIn("on_demand", msg)
        self.assertNotIn("idle_timeout_sec", self._raw("svc-det"))

    def test_unknown_service(self):
        ok, code, _ = self.mgr.update_config("nope", {"idle_timeout_sec": 300})
        self.assertFalse(ok)
        self.assertEqual(code, "not_found")

    def test_float_saved_as_int(self):
        """落盘统一整数，避免 900.0 与 900 混写。"""
        self.mgr.update_config("svc-od", {"idle_timeout_sec": 900.0})
        v = self._rt("svc-od")["idle_timeout_sec"]
        self.assertEqual(v, 900)
        self.assertIsInstance(v, int)   # 必须是整数，不能是 900.0

    def test_other_fields_preserved(self):
        """写运行态只改 runtime 组，声明字段（path/mode/start/stop/health）不变。"""
        before = self._raw("svc-od")
        self.mgr.update_config("svc-od", {"idle_timeout_sec": 1800})
        after = self._raw("svc-od")
        for k in ("path", "mode", "start", "stop", "identity"):
            self.assertEqual(before[k], after[k], f"字段 {k} 被改坏了")
        self.assertEqual(self._rt("svc-od")["idle_timeout_sec"], 1800)

    def test_spec_reloaded_into_memory(self):
        """保存后内存 Spec 同步更新，无需等下一轮 refresh。"""
        self.mgr.update_config("svc-od", {"idle_timeout_sec": 7200})
        self.assertEqual(self.mgr.get_spec("svc-od").idle_timeout, 7200.0)

    def test_runtime_survives_restart(self):
        """重启（新 Manager 实例）后运行态仍在（从注册条重新解析）。"""
        self.mgr.update_config("svc-od", {"idle_timeout_sec": 1200})
        m2 = Manager(Scanner(self.reg), self.root / "order.json")
        self.assertEqual(m2.get_spec("svc-od").idle_timeout, 1200.0)

    def test_runtime_written_back_to_reg(self):
        """改配置后注册条 runtime 组更新，声明字段保持。"""
        self.assertEqual(self._snap("svc-od")["idle_timeout"], 600.0)
        self.mgr.update_config("svc-od", {"idle_timeout_sec": 1800})
        self.assertEqual(self._snap("svc-od")["idle_timeout"], 1800.0)
        self.assertEqual(self._rt("svc-od")["idle_timeout_sec"], 1800)
        self.assertEqual(self._raw("svc-od")["mode"], "on_demand")

    def test_toggle_autostart_writes_reg(self):
        """自启开关写回注册条 runtime.autostart。"""
        ok, msg = self.mgr.toggle_autostart("svc-det", True)
        self.assertTrue(ok, msg)
        self.assertIs(self._snap("svc-det")["autostart"], True)
        self.assertIs(self._rt("svc-det")["autostart"], True)

    def test_on_demand_autostart_still_rejected(self):
        """on_demand 仍不许自启，且不写 runtime.autostart。"""
        ok, msg = self.mgr.toggle_autostart("svc-od", True)
        self.assertFalse(ok)
        self.assertIn("on_demand", msg)
        self.assertFalse(self._rt("svc-od").get("autostart", False))


if __name__ == "__main__":
    unittest.main(verbosity=2)
