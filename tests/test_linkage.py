# -*- coding: utf-8 -*-
"""my-service · tests/test_linkage.py — 服务分组关联 + 服务配置行内编辑

覆盖：
- linkage 的路径归属判定（相等 / 子目录 / 前缀相似不算 / 嵌套取最长 / 无关联）
- Manager.snapshot() 的 cron_names 字段（含 provider 缺失与异常兜底）
- Manager.update_config 的 idle_timeout_sec：保存、边界拒绝、非 on_demand 拒绝、
  未知服务、坏值回滚不留脏注册条

纯标准库 + tempfile 隔离，不触碰真实 reg/ 与 logs/，绝不启动任何进程。
运行：python -m unittest tests.test_linkage -v
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

from core.linkage import is_under, link_crons  # noqa: E402
from core.scheduler import CronSpec  # noqa: E402
from core.scanner import Scanner  # noqa: E402
from core.state import Manager  # noqa: E402


class _Fake:
    """只带 path 的哑对象，用于纯判定测试。"""

    def __init__(self, path):
        self.path = Path(path)


# ---------------------------------------------------------------- 路径判定
class TestIsUnder(unittest.TestCase):
    def test_equal(self):
        self.assertTrue(is_under("D:/svc/a", "D:/svc/a"))

    def test_child(self):
        self.assertTrue(is_under("D:/svc/a/sub", "D:/svc/a"))

    def test_case_insensitive(self):
        self.assertTrue(is_under("D:/SVC/a/sub", "d:/svc/a"))

    def test_similar_prefix_not_matched(self):
        """D:\\svc\\ab 不在 D:\\svc\\a 之下——按分量比较，不是字符串前缀。"""
        self.assertFalse(is_under("D:/svc/ab", "D:/svc/a"))

    def test_different_root(self):
        self.assertFalse(is_under("E:/svc/a", "D:/svc/a"))


class TestLinkCrons(unittest.TestCase):
    def test_groups_by_path(self):
        specs = {"alpha": _Fake("C:/path/to/alpha"),
                 "other": _Fake("C:/path/to/other")}
        crons = {"alpha-snapshot": _Fake("C:/path/to/alpha"),
                 "standalone": _Fake("D:/scripts/misc")}
        got = link_crons(specs, crons)
        self.assertEqual(got, {"alpha": ["alpha-snapshot"]})

    def test_nested_takes_longest(self):
        """服务目录互相嵌套时，任务归属更具体的那个。"""
        specs = {"outer": _Fake("D:/svc"), "inner": _Fake("D:/svc/inner")}
        crons = {"t": _Fake("D:/svc/inner")}
        self.assertEqual(link_crons(specs, crons), {"inner": ["t"]})

    def test_multiple_sorted(self):
        specs = {"a": _Fake("D:/svc/a")}
        crons = {"a-zeta": _Fake("D:/svc/a"), "a-alpha": _Fake("D:/svc/a/sub")}
        self.assertEqual(link_crons(specs, crons), {"a": ["a-alpha", "a-zeta"]})

    def test_no_match_absent(self):
        specs = {"a": _Fake("D:/svc/a")}
        crons = {"x": _Fake("D:/nowhere")}
        self.assertEqual(link_crons(specs, crons), {})


# ------------------------------------------------- snapshot 的 cron_names
class TestSnapshotCronNames(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="mslink-test-"))
        self.reg = self.root / "reg"
        self.reg.mkdir()
        self.home = self.root / "svc-a"
        self.home.mkdir()
        (self.home / "noop.ps1").write_text("# noop\n", encoding="utf-8")
        (self.reg / "svc-a.json").write_text(json.dumps(
            {"path": str(self.home), "entry": "noop.ps1"}), encoding="utf-8")
        self.mgr = Manager(Scanner(self.reg), self.root / "order.json")
        self.mgr.refresh()

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _one(self):
        return self.mgr.snapshot()[0]

    def test_absent_without_provider(self):
        self.assertEqual(self._one()["cron_names"], [])

    def test_with_provider(self):
        self.mgr.cron_specs_provider = lambda: {"t1": _Fake(self.home)}
        self.assertEqual(self._one()["cron_names"], ["t1"])

    def test_provider_exception_swallowed(self):
        """调度器炸了也不能拖垮面板。"""
        def _boom():
            raise RuntimeError("boom")
        self.mgr.cron_specs_provider = _boom
        self.assertEqual(self._one()["cron_names"], [])


# ------------------------------------------------- update_config
class TestServiceUpdateConfig(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="mscfg-test-"))
        self.reg = self.root / "reg"
        self.reg.mkdir()
        self.home = self.root / "svc-od"
        self.home.mkdir()
        (self.home / "server.py").write_text("print(1)\n", encoding="utf-8")
        self._write("svc-od", path=str(self.home), entry="server.py", mode="on_demand",
                    health={"type": "http", "url": "http://127.0.0.1:8799/health"},
                    idle_timeout_sec=600, autostart=False)
        self.home2 = self.root / "svc-det"
        self.home2.mkdir()
        (self.home2 / "start.ps1").write_text("# noop\n", encoding="utf-8")
        (self.home2 / "stop.ps1").write_text("# noop\n", encoding="utf-8")
        # detached 硬约束：必须给 stop_script，且健康检查只能是 port/http
        self._write("svc-det", path=str(self.home2), entry="start.ps1",
                    mode="detached", stop_script="stop.ps1",
                    health={"type": "port", "port": 8798}, autostart=False)
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
        """运行态账本条目（data\\service_runtime.json 的沙箱等价物）；无记录即空。"""
        try:
            data = json.loads((self.root / "service_runtime.json")
                              .read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            data = {}
        return data.get(name, {})

    def _reg_bytes(self, name):
        return (self.reg / f"{name}.json").read_bytes()

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _snap(self, name):
        return next(s for s in self.mgr.snapshot() if s["name"] == name)

    # ---- 成功路径 ----
    def test_save_idle(self):
        """改空闲回收时长：只写运行态账本，注册条一个字节不动。"""
        before = self._reg_bytes("svc-od")
        ok, code, msg = self.mgr.update_config("svc-od", {"idle_timeout_sec": 900})
        self.assertTrue(ok, msg)
        self.assertEqual(self._rt("svc-od")["idle_timeout_sec"], 900)
        self.assertEqual(self._snap("svc-od")["idle_timeout"], 900.0)
        self.assertEqual(self._reg_bytes("svc-od"), before, "注册条不该被改写")

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
        self.assertNotIn("idle_timeout_sec", self._rt("svc-od"), "越界值不得落账本")

    def test_above_max_rejected(self):
        ok, code, _ = self.mgr.update_config("svc-od", {"idle_timeout_sec": 999999})
        self.assertFalse(ok)
        self.assertEqual(code, "invalid")
        self.assertNotIn("idle_timeout_sec", self._rt("svc-od"))

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
        """运行态写入不得碰注册条任何内容（含 mode/health/path、autostart 默认值）。"""
        before = self._raw("svc-od")
        before_bytes = self._reg_bytes("svc-od")
        self.mgr.update_config("svc-od", {"idle_timeout_sec": 1800})
        after = self._raw("svc-od")
        for k in ("path", "mode", "health", "entry", "autostart"):
            self.assertEqual(before[k], after[k], f"字段 {k} 被改坏了")
        self.assertEqual(self._reg_bytes("svc-od"), before_bytes, "注册条不该被改写")
        self.assertEqual(self._rt("svc-od")["idle_timeout_sec"], 1800)

    def test_spec_reloaded_into_memory(self):
        """保存后内存 Spec 同步更新，无需等下一轮 refresh。"""
        self.mgr.update_config("svc-od", {"idle_timeout_sec": 7200})
        self.assertEqual(self.mgr.get_spec("svc-od").idle_timeout, 7200.0)

    def test_runtime_survives_restart(self):
        """重启（新 Manager 实例）后运行态仍在，注册条字节不变。"""
        before = self._reg_bytes("svc-od")
        self.mgr.update_config("svc-od", {"idle_timeout_sec": 1200})
        m2 = Manager(Scanner(self.reg), self.root / "order.json")
        self.assertEqual(m2.get_spec("svc-od").idle_timeout, 1200.0)
        self.assertEqual(self._reg_bytes("svc-od"), before)

    def test_ledger_overrides_reg_field(self):
        """账本与注册条同时有 idle_timeout_sec：账本胜，注册条里的旧值不动。"""
        self.assertEqual(self._snap("svc-od")["idle_timeout"], 600.0)   # 夹具旧值
        self.mgr.update_config("svc-od", {"idle_timeout_sec": 1800})
        self.assertEqual(self._snap("svc-od")["idle_timeout"], 1800.0)
        self.assertEqual(self._raw("svc-od")["idle_timeout_sec"], 600)

    def test_toggle_autostart_writes_ledger_only(self):
        """自启开关只写账本：注册条（含其中的 autostart 旧值）一个字节不动。"""
        before = self._reg_bytes("svc-det")
        ok, msg = self.mgr.toggle_autostart("svc-det", True)
        self.assertTrue(ok, msg)
        self.assertIs(self._snap("svc-det")["autostart"], True)
        self.assertEqual(self._reg_bytes("svc-det"), before, "注册条不该被改写")
        self.assertIs(self._rt("svc-det")["autostart"], True)

    def test_on_demand_autostart_still_rejected(self):
        """on_demand 仍不许自启（校验不受分离影响），且不写账本。"""
        ok, msg = self.mgr.toggle_autostart("svc-od", True)
        self.assertFalse(ok)
        self.assertIn("on_demand", msg)
        self.assertEqual(self._rt("svc-od"), {})


class TestCronSpecStillUsable(unittest.TestCase):
    """CronSpec 与 link_crons 的兼容：真实 CronSpec 有 path 字段。"""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="mslink2-test-"))
        self.reg = self.root / "cron"
        self.reg.mkdir()

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_realspec_paths(self):
        (self.root / "run.py").write_text("print(1)\n", encoding="utf-8")
        (self.reg / "t.json").write_text(json.dumps({
            "path": str(self.root), "entry": "run.py",
            "schedule": {"type": "daily", "at": "08:30"},
        }), encoding="utf-8")
        spec = CronSpec("t", self.reg / "t.json")
        self.assertTrue(spec.valid)
        got = link_crons({"svc": _Fake(self.root)}, {"t": spec})
        self.assertEqual(got, {"svc": ["t"]})


if __name__ == "__main__":
    unittest.main(verbosity=2)
