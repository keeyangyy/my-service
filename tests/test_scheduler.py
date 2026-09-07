# -*- coding: utf-8 -*-
"""my-service · tests/test_scheduler.py — 定时任务调度核心回归

纯标准库 unittest（项目零第三方依赖）。全部用 tempfile 隔离，
不触碰 reg\\ 与 logs\\；audit 未 init → 事件只构造不落盘。

运行：python -m unittest tests.test_scheduler -v   （在 my-service 根目录）
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from core.scheduler import CronSpec, Scheduler, _next_daily  # noqa: E402


class _Sandbox:
    """临时沙箱：注册目录 + 一个"写一行时间戳"的测试脚本。"""

    def __init__(self):
        self.root = Path(tempfile.mkdtemp(prefix="msched-test-"))
        self.reg = self.root / "cron"
        self.reg.mkdir()
        (self.root / "write.py").write_text(
            "import time\n"
            "from pathlib import Path\n"
            "with open(Path(__file__).parent / 'runs.log', 'a') as f:\n"
            "    f.write(str(time.time()) + '\\n')\n",
            encoding="utf-8")
        (self.root / "sleep.py").write_text("import time; time.sleep(30)\n",
                                            encoding="utf-8")

    def put(self, name: str, **kw) -> Path:
        data = {"path": str(self.root), "entry": "write.py"}
        data.update(kw)
        p = self.reg / f"{name}.json"
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return p

    def sched(self) -> Scheduler:
        return Scheduler(self.reg, self.root / "state.json")

    @staticmethod
    def wait_running(sched: Scheduler, name: str, timeout: float = 5.0) -> bool:
        """轮询等待异步执行线程进入 running（tick 发射是异步的，有窗口）。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if name in sched.running:
                return True
            time.sleep(0.05)
        return False

    def cleanup(self):
        import shutil
        shutil.rmtree(self.root, ignore_errors=True)


class TestCronSpec(unittest.TestCase):
    def setUp(self):
        self.box = _Sandbox()

    def tearDown(self):
        self.box.cleanup()

    def test_interval_ok(self):
        p = self.box.put("t1", schedule={"type": "interval", "every_sec": 8})
        s = CronSpec("t1", p)
        self.assertTrue(s.valid, s.error)
        self.assertEqual(s.schedule_desc, "每 8 秒")
        self.assertFalse(s.catch_up)
        self.assertAlmostEqual(s.timeout_sec, 300.0)

    def test_daily_ok(self):
        p = self.box.put("t2", schedule={"type": "daily", "at": "08:30"})
        s = CronSpec("t2", p)
        self.assertTrue(s.valid, s.error)
        self.assertEqual(s.schedule_at, (8, 30))
        self.assertEqual(s.schedule_desc, "每天 08:30")

    def test_invalid_missing_schedule(self):
        p = self.box.put("t3")
        s = CronSpec("t3", p)
        self.assertFalse(s.valid)
        self.assertIn("schedule", s.error)

    def test_invalid_daily_bad_at(self):
        p = self.box.put("t4", schedule={"type": "daily", "at": "25:99"})
        s = CronSpec("t4", p)
        self.assertFalse(s.valid)
        self.assertIn("at", s.error)

    def test_interval_too_small(self):
        p = self.box.put("t5", schedule={"type": "interval", "every_sec": 1})
        s = CronSpec("t5", p)
        self.assertFalse(s.valid)
        self.assertIn("every_sec", s.error)

    def test_timeout_clamped(self):
        p = self.box.put("t6", schedule={"type": "interval", "every_sec": 10},
                         timeout_sec=1)
        s = CronSpec("t6", p)
        self.assertTrue(s.valid, s.error)
        self.assertEqual(s.timeout_sec, 5.0)   # 下限钳到 5s


class TestScheduling(unittest.TestCase):
    def setUp(self):
        self.box = _Sandbox()
        self.box.put("t", schedule={"type": "interval", "every_sec": 5})
        self.sched = self.box.sched()

    def tearDown(self):
        self.box.cleanup()

    def _snap(self, name="t"):
        return {s["name"]: s for s in self.sched.snapshot()}[name]

    def test_new_task_next_run_is_one_period_ahead(self):
        self.sched.scan()
        self.sched.tick()   # 只初始化计划，未到点
        nxt = self._snap()["next_run"]
        self.assertAlmostEqual(nxt - time.time(), 5, delta=3)

    def test_due_task_fires_and_reschedules(self):
        self.sched.scan()
        self.sched.tick()
        self.sched.persist["t"]["next_run"] = time.time() - 1   # 人为到点
        self.sched.tick()
        self.assertTrue(self.box.wait_running(self.sched, "t"))  # 已发射
        deadline = time.time() + 10
        while time.time() < deadline and "t" in self.sched.running:
            time.sleep(0.2)
        snap = self._snap()
        self.assertTrue(snap["last_ok"], snap)
        self.assertEqual(snap["last_rc"], 0)
        # 跑完后重排：按上次起算 + 周期
        self.assertAlmostEqual(snap["next_run"] - snap["last_run"], 5, delta=2)

    def test_miss_deferred_without_catch_up(self):
        self.sched.scan()
        self.sched.tick()
        self.sched.persist["t"]["next_run"] = time.time() - 999   # 远超 GRACE
        self.sched.tick()
        snap = self._snap()
        self.assertNotIn("t", self.sched.running)                # 未补跑
        self.assertGreater(snap["next_run"], time.time())        # 顺延到未来
        self.assertAlmostEqual(snap["next_run"] - time.time(), 5, delta=3)

    def test_miss_caught_up_when_enabled(self):
        self.box.put("c", schedule={"type": "interval", "every_sec": 3600},
                     catch_up=True)
        self.sched.scan()
        self.sched.tick()
        self.sched.persist["c"]["next_run"] = time.time() - 999
        self.sched.tick()
        self.assertTrue(self.box.wait_running(self.sched, "c"))  # 立即补跑
        deadline = time.time() + 10
        while time.time() < deadline and "c" in self.sched.running:
            time.sleep(0.2)
        self.assertTrue(self._snap("c")["last_ok"])

    def test_disabled_task_not_scheduled(self):
        self.box.put("off", enabled=False,
                     schedule={"type": "interval", "every_sec": 5})
        self.sched.scan()
        self.sched.tick()
        self.assertIsNone(self._snap("off")["next_run"])

    def test_no_pileup_while_running(self):
        # 上一轮未完（running 中）：到点不触发，跑完后经"错过"处理顺延，不连发
        self.sched.scan()
        self.sched.tick()
        self.sched.running.add("t")
        before = time.time()
        self.sched.persist["t"]["next_run"] = before - 1
        self.sched.tick()
        self.assertEqual(self._snap()["next_run"], before - 1)   # 原计划未动
        self.sched.running.discard("t")


class TestRunNowAndPersist(unittest.TestCase):
    def setUp(self):
        self.box = _Sandbox()
        self.box.put("t", schedule={"type": "interval", "every_sec": 3600})
        self.sched = self.box.sched()
        self.sched.scan()

    def tearDown(self):
        self.box.cleanup()

    def test_run_now_codes(self):
        ok, code, _ = self.sched.run_now("t")
        self.assertTrue(ok and code == "ok")
        ok2, code2, _ = self.sched.run_now("t")        # 运行中 → busy
        self.assertFalse(ok2 and code2 == "busy")
        self.assertEqual(self.sched.run_now("ghost")[1], "not_found")

    def test_state_persisted_and_restored(self):
        ok, _, _ = self.sched.run_now("t")
        self.assertTrue(ok)
        self.assertTrue(self.box.wait_running(self.sched, "t"))   # 先等发射
        deadline = time.time() + 10
        while time.time() < deadline and "t" in self.sched.running:
            time.sleep(0.2)                                       # 再等完成
        # 新实例（模拟宿主重启）应从账本恢复 last_run/next_run
        s2 = self.box.sched()
        s2.scan()                     # 宿主流程中 tick 每秒都会 scan，此处显式补一次
        snap = {x["name"]: x for x in s2.snapshot()}["t"]
        self.assertTrue(snap["last_ok"], snap)
        self.assertEqual(snap["last_rc"], 0)
        self.assertGreater(snap["next_run"], time.time())

    def test_registration_removed_cleans_ledger(self):
        ok, _, _ = self.sched.run_now("t")
        self.assertTrue(ok)
        deadline = time.time() + 10
        while time.time() < deadline and "t" in self.sched.running:
            time.sleep(0.2)
        (self.box.reg / "t.json").unlink()
        self.sched.scan()                               # 注册条已删 → 账目清零
        self.assertNotIn("t", self.sched.persist)

    def test_toggle_enabled_writes_back_and_takes_effect(self):
        ok, _ = self.sched.toggle_enabled("t", False)
        self.assertTrue(ok)
        # 权威值回写注册条
        raw = json.loads((self.box.reg / "t.json").read_text(encoding="utf-8"))
        self.assertIs(raw["enabled"], False)
        # 内存态同步 → tick 不再调度
        snap = {x["name"]: x for x in self.sched.snapshot()}["t"]
        self.assertIs(snap["enabled"], False)
        self.assertIsNone(snap["next_run"])             # 未初始化/不再推进
        self.sched.tick()
        snap = {x["name"]: x for x in self.sched.snapshot()}["t"]
        self.assertIsNone(snap["next_run"])             # 停用期间不排新计划
        # 重新启用 → tick 恢复排程
        ok2, _ = self.sched.toggle_enabled("t", True)
        self.assertTrue(ok2)
        self.sched.tick()
        snap = {x["name"]: x for x in self.sched.snapshot()}["t"]
        self.assertTrue(snap["enabled"] and snap["next_run"])

    def test_toggle_enabled_unknown_task(self):
        ok, msg = self.sched.toggle_enabled("ghost", False)
        self.assertFalse(ok)
        self.assertIn("未知", msg)


class TestDailyMath(unittest.TestCase):
    def test_next_daily_today_future(self):
        now = datetime.now()
        target = now.replace(hour=0, minute=0, second=0, microsecond=0)
        nxt = _next_daily((23, 59), target.timestamp())
        dt = datetime.fromtimestamp(nxt)
        self.assertEqual((dt.hour, dt.minute), (23, 59))
        self.assertGreater(nxt, target.timestamp())

    def test_next_daily_rolls_to_tomorrow(self):
        now = datetime.now()
        target = now.replace(hour=23, minute=59, second=0, microsecond=0)
        nxt = _next_daily((0, 0), target.timestamp())
        dt = datetime.fromtimestamp(nxt)
        self.assertEqual((dt.hour, dt.minute), (0, 0))
        self.assertEqual(dt.date(), (target + timedelta(days=1)).date())


if __name__ == "__main__":
    unittest.main(verbosity=2)
