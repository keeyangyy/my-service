"""my-service · tests/test_scheduler.py — 定时任务调度核心回归

纯标准库 unittest（项目零第三方依赖）。全部用 tempfile 隔离，
不触碰 reg/ 与 data/、logs/；audit 未 init → 事件只构造不落盘。

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

from core.scheduler import CronSpec, Scheduler, _next_daily, _next_weekly  # noqa: E402


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
        entry = kw.pop("entry", "write.py")
        data = {"path": str(self.root), "mode": "task",
                "start": {"entry": entry}}
        data.update(kw)
        p = self.reg / f"{name}.json"
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return p

    def sched(self) -> Scheduler:
        return Scheduler(self.reg, self.root / "state.json",
                         self.root / "order.json")

    def settings(self, name: str | None = None) -> dict:
        """读注册条 settings 组；给 name 则返回该条。"""
        out: dict = {}
        for p in self.reg.glob("*.json"):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            out[p.stem] = data.get("settings") or {}
        return out.get(name, {}) if name else out

    def reg_bytes(self, name: str) -> bytes:
        """注册条原始字节：断言"运行态写入一个字节都不动"用。"""
        return (self.reg / f"{name}.json").read_bytes()

    @staticmethod
    def wait_running(sched: Scheduler, name: str, timeout: float = 10.0) -> bool:
        """轮询等待任务真正被触发。

        tick 发射是异步的，而任务可能在首次轮询前就跑完并从 `running` 退出——
        所以「已在 running」和「last_run 已落账」都算已触发，避免慢机/快任务下的假失败。
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            if name in sched.running:
                return True
            if (sched.persist.get(name) or {}).get("last_run"):
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
        p = self.box.put("t1", schedule={"type": "interval", "every_sec": 10})
        s = CronSpec("t1", p)
        self.assertTrue(s.valid, s.error)
        self.assertEqual(s.schedule_desc, "每 10 秒")
        self.assertFalse(s.catch_up)
        self.assertAlmostEqual(s.timeout_sec, 300.0)

    def test_daily_ok(self):
        p = self.box.put("t2", schedule={"type": "daily", "at": "08:30"})
        s = CronSpec("t2", p)
        self.assertTrue(s.valid, s.error)
        self.assertEqual(s.schedule_times, [(8, 30)])
        self.assertEqual(s.schedule_desc, "每天 08:30")

    def test_daily_multi_at_parsed_and_deduped(self):
        p = self.box.put("t2m", schedule={"type": "daily",
                                          "at": ["23:30", "09:30", "09:30"]})
        s = CronSpec("t2m", p)
        self.assertTrue(s.valid, s.error)
        self.assertEqual(s.schedule_times, [(9, 30), (23, 30)])
        self.assertEqual(s.schedule_desc, "每天 09:30, 23:30")

    def test_daily_empty_at_defaults_to_0900(self):
        p = self.box.put("t2e", schedule={"type": "daily", "at": []})
        s = CronSpec("t2e", p)
        self.assertTrue(s.valid, s.error)
        self.assertEqual(s.schedule_times, [(9, 0)])

    def test_weekly_ok(self):
        p = self.box.put("tw", schedule={"type": "weekly",
                                         "days": [5, 1, 3, 1], "at": ["09:30"]})
        s = CronSpec("tw", p)
        self.assertTrue(s.valid, s.error)
        self.assertEqual(s.schedule_type, "weekly")
        self.assertEqual(s.schedule_days, [0, 2, 4])   # 内部 weekday：周一/三/五
        self.assertEqual(s.schedule_times, [(9, 30)])
        self.assertEqual(s.schedule_desc, "每周周一、周三、周五 09:30")

    def test_weekly_empty_days_means_no_schedule(self):
        p = self.box.put("tw0", schedule={"type": "weekly", "days": [],
                                          "at": ["09:30"]})
        s = CronSpec("tw0", p)
        self.assertTrue(s.valid, s.error)
        self.assertFalse(s.schedule_active)
        self.assertEqual(s.schedule_desc, "每周（未选择星期，不执行）")

    def test_weekly_bad_days_rejected(self):
        p = self.box.put("tw9", schedule={"type": "weekly", "days": [0, 8],
                                          "at": ["09:30"]})
        s = CronSpec("tw9", p)
        self.assertFalse(s.valid)
        self.assertIn("days", s.error)

    def test_weekly_empty_at_defaults_to_0900(self):
        p = self.box.put("twa", schedule={"type": "weekly", "days": [1],
                                          "at": []})
        s = CronSpec("twa", p)
        self.assertTrue(s.valid, s.error)
        self.assertEqual(s.schedule_times, [(9, 0)])

    def test_missing_schedule_defaults_to_daily_0900(self):
        p = self.box.put("t3")
        s = CronSpec("t3", p)
        self.assertTrue(s.valid, s.error)
        self.assertEqual(s.schedule_type, "daily")
        self.assertEqual(s.schedule_desc, "每天 09:00")

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
                         settings={"run_timeout_sec": 1})
        s = CronSpec("t6", p)
        self.assertTrue(s.valid, s.error)
        self.assertEqual(s.timeout_sec, 5.0)   # 下限钳到 5s


class TestScheduling(unittest.TestCase):
    def setUp(self):
        self.box = _Sandbox()
        self.box.put("t", schedule={"type": "interval", "every_sec": 10},
                     settings={"state": "active"})
        self.sched = self.box.sched()

    def tearDown(self):
        self.box.cleanup()

    def _snap(self, name="t"):
        return {s["name"]: s for s in self.sched.snapshot()}[name]

    def test_new_task_next_run_is_one_period_ahead(self):
        self.sched.scan()
        self.sched.tick()   # 只初始化计划，未到点
        nxt = self._snap()["next_run"]
        self.assertAlmostEqual(nxt - time.time(), 10, delta=3)

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
        self.assertAlmostEqual(snap["next_run"] - snap["last_run"], 10, delta=2)

    def test_miss_deferred_without_catch_up(self):
        self.sched.scan()
        self.sched.tick()
        self.sched.persist["t"]["next_run"] = time.time() - 999   # 远超 GRACE
        self.sched.tick()
        snap = self._snap()
        self.assertNotIn("t", self.sched.running)                # 未补跑
        self.assertGreater(snap["next_run"], time.time())        # 顺延到未来
        self.assertAlmostEqual(snap["next_run"] - time.time(), 10, delta=3)

    def test_miss_caught_up_when_enabled(self):
        self.box.put("c", schedule={"type": "interval", "every_sec": 3600},
                     settings={"state": "active", "catch_up": True})
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
        self.box.put("off",
                     schedule={"type": "interval", "every_sec": 10})
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
        self.box.put("t", schedule={"type": "interval", "every_sec": 3600},
                     settings={"state": "active"})
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

    def test_toggle_enabled_writes_settings_and_takes_effect(self):
        ok, _ = self.sched.toggle_enabled("t", False)
        self.assertTrue(ok)
        # 权威值写回注册条 settings.state
        self.assertEqual(self.box.settings("t")["state"], "disabled")
        # 内存态同步 → tick 不再调度
        snap = {x["name"]: x for x in self.sched.snapshot()}["t"]
        self.assertEqual(snap["state"], "disabled")
        self.sched.tick()
        snap = {x["name"]: x for x in self.sched.snapshot()}["t"]
        self.assertIsNone(snap["next_run"])             # 停用期间不排新计划
        # 重新启用 → tick 恢复排程
        ok2, _ = self.sched.toggle_enabled("t", True)
        self.assertTrue(ok2)
        self.sched.tick()
        snap = {x["name"]: x for x in self.sched.snapshot()}["t"]
        self.assertEqual(snap["state"], "active")
        self.assertTrue(snap["next_run"])

    def test_toggle_enabled_unknown_task(self):
        ok, msg = self.sched.toggle_enabled("ghost", False)
        self.assertFalse(ok)
        self.assertIn("未知", msg)


class TestDailyMath(unittest.TestCase):
    def test_next_daily_today_future(self):
        now = datetime.now()
        target = now.replace(hour=0, minute=0, second=0, microsecond=0)
        nxt = _next_daily([(23, 59)], target.timestamp())
        dt = datetime.fromtimestamp(nxt)
        self.assertEqual((dt.hour, dt.minute), (23, 59))
        self.assertGreater(nxt, target.timestamp())

    def test_next_daily_rolls_to_tomorrow(self):
        now = datetime.now()
        target = now.replace(hour=23, minute=59, second=0, microsecond=0)
        nxt = _next_daily([(0, 0)], target.timestamp())
        dt = datetime.fromtimestamp(nxt)
        self.assertEqual((dt.hour, dt.minute), (0, 0))
        self.assertEqual(dt.date(), (target + timedelta(days=1)).date())

    def test_next_daily_multiple_times(self):
        # now=10:00 → 今天下一个 12:00；全过 → 明天最早 08:00
        target = datetime(2026, 1, 1, 10, 0, 0)
        nxt = _next_daily([(8, 0), (12, 0), (23, 0)], target.timestamp())
        self.assertEqual(datetime.fromtimestamp(nxt),
                         datetime(2026, 1, 1, 12, 0, 0))
        nxt2 = _next_daily([(8, 0), (12, 0)],
                           datetime(2026, 1, 1, 23, 30, 0).timestamp())
        self.assertEqual(datetime.fromtimestamp(nxt2),
                         datetime(2026, 1, 2, 8, 0, 0))


class TestWeeklyMath(unittest.TestCase):
    """每周固定天：内部 weekday 0=周一 … 6=周日。"""

    def test_same_day_future_time(self):
        base = datetime(2026, 11, 16, 8, 0)      # 周一
        nxt = _next_weekly([0], [(9, 30)], base.timestamp())
        self.assertEqual(datetime.fromtimestamp(nxt),
                         datetime(2026, 11, 16, 9, 30))

    def test_next_matching_day(self):
        base = datetime(2026, 11, 16, 12, 0)     # 周一 12:00
        nxt = _next_weekly([2], [(9, 30)], base.timestamp())   # 周三
        self.assertEqual(datetime.fromtimestamp(nxt),
                         datetime(2026, 11, 18, 9, 30))

    def test_wraps_to_next_week(self):
        base = datetime(2026, 11, 16, 12, 0)     # 周一 12:00
        nxt = _next_weekly([0], [(9, 30)], base.timestamp())
        self.assertEqual(datetime.fromtimestamp(nxt),
                         datetime(2026, 11, 23, 9, 30))

    def test_multiple_days_and_times(self):
        base = datetime(2026, 11, 16, 12, 0)     # 周一 12:00
        nxt = _next_weekly([0, 2], [(9, 30), (18, 0)], base.timestamp())
        self.assertEqual(datetime.fromtimestamp(nxt),
                         datetime(2026, 11, 16, 18, 0))   # 当天 18:00 更近


class TestUpdateConfig(unittest.TestCase):
    """面板行内编辑：schedule / timeout_sec / catch_up / enabled 补丁写回。"""

    def setUp(self):
        self.box = _Sandbox()
        self.box.put("d", schedule={"type": "daily", "at": "23:00"},
                     settings={"state": "active"},
                     display="每日快照", description="带额外字段的任务")
        self.box.put("i", schedule={"type": "interval", "every_sec": 600},
                     settings={"state": "active"})
        self.sched = self.box.sched()
        self.sched.scan()
        self.sched.tick()          # 初始化 next_run

    def tearDown(self):
        self.box.cleanup()

    def _raw(self, name):
        return json.loads((self.box.reg / f"{name}.json").read_text(encoding="utf-8"))

    def _snap(self, name):
        return {x["name"]: x for x in self.sched.snapshot()}[name]

    def test_change_daily_at_rewrites_and_reschedules(self):
        ok, code, _ = self.sched.update_config("d", {"schedule":
                                {"type": "daily", "at": "07:15"}})
        self.assertTrue(ok, code)
        # 新时刻只进运行态账本；注册条里留的是"默认计划"，一个字节不动
        self.assertEqual(self._raw("d")["schedule"]["at"], "07:15")
        snap = self._snap("d")
        self.assertEqual(snap["at"], ["07:15"])
        self.assertEqual(snap["schedule"], "每天 07:15")
        # next_run 必须按新时刻重算（否则面板显示新时间、实际旧时间触发）
        self.assertEqual(datetime.fromtimestamp(snap["next_run"]).hour, 7)
        self.assertEqual(datetime.fromtimestamp(snap["next_run"]).minute, 15)

    def test_change_daily_multi_at(self):
        ok, code, _ = self.sched.update_config(
            "d", {"schedule": {"type": "daily", "at": ["09:30", "21:00"]}})
        self.assertTrue(ok, code)
        self.assertEqual(self._raw("d")["schedule"]["at"],
                         ["09:30", "21:00"])
        snap = self._snap("d")
        self.assertEqual(snap["at"], ["09:30", "21:00"])
        self.assertEqual(snap["schedule"], "每天 09:30, 21:00")

    def test_change_interval_every_sec(self):
        ok, code, _ = self.sched.update_config("i", {"schedule":
                                {"type": "interval", "every_sec": 1800}})
        self.assertTrue(ok, code)
        self.assertEqual(self._raw("i")["schedule"]["every_sec"], 1800)
        self.assertEqual(self._snap("i")["schedule"], "每 30 分钟")

    def test_change_timeout_and_catch_up(self):
        ok, _, _ = self.sched.update_config(
            "d", {"timeout_sec": 60, "catch_up": True})
        self.assertTrue(ok)
        rt = self.box.settings("d")
        self.assertEqual(rt["run_timeout_sec"], 60.0)
        self.assertIs(rt["catch_up"], True)
        snap = self._snap("d")
        self.assertEqual(snap["timeout_sec"], 60.0)
        self.assertIs(snap["catch_up"], True)

    def test_invalid_schedule_rolls_back(self):
        before = self._raw("d")
        ok, code, msg = self.sched.update_config(
            "d", {"schedule": {"type": "daily", "at": "99:99"}})
        self.assertFalse(ok)
        self.assertEqual(code, "invalid")
        self.assertIn("回滚", msg)
        # 注册条里仍是原计划（写后校验失败即回滚，不留半截配置）
        self.assertEqual(self._raw("d")["schedule"], before["schedule"])
        snap = self._snap("d")
        self.assertTrue(snap["valid"], snap["error"])
        self.assertEqual(snap["at"], ["23:00"])

    def test_interval_below_floor_rolls_back(self):
        ok, code, _ = self.sched.update_config(
            "i", {"schedule": {"type": "interval", "every_sec": 2}})
        self.assertFalse(ok and code == "invalid")
        self.assertNotIn("schedule", self.box.settings("i"))
        self.assertEqual(self._raw("i")["schedule"]["every_sec"], 600)

    def test_unknown_type_and_task(self):
        ok, code, _ = self.sched.update_config(
            "d", {"schedule": {"type": "weekly", "at": "08:00"}})
        self.assertFalse(ok and code == "invalid")
        self.assertEqual(self.box.settings("d"), {"state": "active"},
                         "粗校验失败不该写注册条")
        self.assertEqual(self._raw("d")["schedule"]["type"], "daily")
        self.assertEqual(self.sched.update_config("ghost", {"catch_up": True})[1],
                         "not_found")

    def test_enabled_patch_still_works_via_toggle(self):
        ok, msg = self.sched.toggle_enabled("d", False)
        self.assertTrue(ok, msg)
        self.assertEqual(self.box.settings("d")["state"], "disabled")
        self.assertEqual(self._snap("d")["state"], "disabled")

    def test_other_fields_preserved_on_write(self):
        """运行态写入不得碰注册条任何字段（display/entry/path/schedule 默认计划都在）。"""
        self.sched.update_config("d", {"timeout_sec": 45})
        raw = self._raw("d")
        for key in ("path", "start", "schedule", "display"):
            self.assertIn(key, raw)


class TestRuntimeInReg(unittest.TestCase):
    """运行态写回注册条：schedule/runtime 更新、重启保持、旧字段拒绝。"""

    def setUp(self):
        self.box = _Sandbox()
        self.box.put("t", schedule={"type": "daily", "at": "23:00"},
                     settings={"state": "active"})
        self.sched = self.box.sched()
        self.sched.scan()

    def tearDown(self):
        self.box.cleanup()

    def test_schedule_written_back_to_reg(self):
        self.sched.update_config("t", {"schedule": {"type": "daily", "at": "06:30"}})
        raw = json.loads((self.box.reg / "t.json").read_text(encoding="utf-8"))
        self.assertEqual(raw["schedule"]["at"], "06:30")
        s2 = self.box.sched()
        s2.scan()
        self.assertEqual(s2.snapshot()[0]["at"], ["06:30"])

    def test_settings_defaults_without_reg(self):
        s = self.box.sched()
        s.scan()
        spec = s.specs["t"]
        self.assertEqual(spec.state, "active")      # 注册条写了 settings.state
        self.assertEqual(spec.timeout_sec, 300.0)
        self.assertIs(spec.catch_up, False)

    def test_task_disabled_by_default(self):
        self.box.put("d0", schedule={"type": "interval", "every_sec": 600})
        s = self.box.sched()
        s.scan()
        self.assertEqual(s.specs["d0"].state, "disabled")

    def test_old_reg_fields_rejected(self):
        self.box.put("legacy", schedule={"type": "interval", "every_sec": 600},
                     enabled=False, timeout_sec=45)
        s = self.box.sched()
        s.scan()
        self.assertFalse(s.specs["legacy"].valid)
        self.assertIn("旧版字段", s.specs["legacy"].error)

    def test_settings_in_reg_is_authoritative(self):
        self.box.put("r", schedule={"type": "interval", "every_sec": 600},
                     settings={"state": "disabled", "catch_up": True,
                               "run_timeout_sec": 120})
        s = self.box.sched()
        s.scan()
        spec = s.specs["r"]
        self.assertEqual(spec.state, "disabled")
        self.assertIs(spec.catch_up, True)
        self.assertEqual(spec.timeout_sec, 120.0)

    def test_registration_removed_clears_spec(self):
        self.sched.update_config("t", {"timeout_sec": 90})
        (self.box.reg / "t.json").unlink()
        self.sched.scan()
        self.assertNotIn("t", self.sched.specs)


class TestOrdering(unittest.TestCase):
    """面板拖拽排序：顺序持久化、新任务不打乱、注销自动清理。"""

    def setUp(self):
        self.box = _Sandbox()
        for n in ("a-task", "b-task", "c-task"):
            self.box.put(n, schedule={"type": "interval", "every_sec": 3600})
        self.sched = self.box.sched()
        self.sched.scan()

    def tearDown(self):
        self.box.cleanup()

    def _names(self):
        return [x["name"] for x in self.sched.snapshot()]

    def test_default_is_alphabetical(self):
        self.assertEqual(self._names(), ["a-task", "b-task", "c-task"])

    def test_set_order_reorders(self):
        ok, code, _ = self.sched.set_order(["c-task", "a-task", "b-task"])
        self.assertTrue(ok)
        self.assertEqual(self._names(), ["c-task", "a-task", "b-task"])

    def test_unknown_names_ignored_and_deduped(self):
        self.sched.set_order(["ghost", "b-task", "b-task", "a-task"])
        self.assertEqual(self._names(), ["b-task", "a-task", "c-task"])

    def test_partial_list_appends_rest(self):
        self.sched.set_order(["c-task"])          # 只提一个，其余按字母序追加
        self.assertEqual(self._names(), ["c-task", "a-task", "b-task"])

    def test_order_persisted_across_restart(self):
        self.sched.set_order(["b-task", "c-task", "a-task"])
        s2 = self.box.sched()
        s2.scan()
        self.assertEqual([x["name"] for x in s2.snapshot()],
                         ["b-task", "c-task", "a-task"])

    def test_new_task_appended_not_disrupting(self):
        self.sched.set_order(["c-task", "b-task", "a-task"])
        self.box.put("d-new", schedule={"type": "interval", "every_sec": 3600})
        self.sched.scan()
        self.assertEqual(self._names(), ["c-task", "b-task", "a-task", "d-new"])

    def test_removed_task_cleaned_from_order(self):
        self.sched.set_order(["c-task", "b-task", "a-task"])
        (self.box.reg / "b-task.json").unlink()
        self.sched.scan()
        self.assertEqual(self.sched._order, ["c-task", "a-task"])
        self.assertEqual(self._names(), ["c-task", "a-task"])
        # 顺序写回注册条 settings.order（重启后不会复活已注销任务）
        saved = {
            n: json.loads((self.box.reg / f"{n}.json").read_text(encoding="utf-8"))
            .get("settings", {}).get("order")
            for n in ("c-task", "a-task")
        }
        self.assertEqual(saved, {"c-task": 1, "a-task": 2})

    def test_no_order_path_still_works(self):
        """未提供 order 文件路径时（如单测/脚本直调）退化为字母序，不报错。"""
        s = Scheduler(self.box.reg, self.box.root / "state2.json")
        s.scan()
        self.assertEqual([x["name"] for x in s.snapshot()],
                         ["a-task", "b-task", "c-task"])
        s.set_order(["c-task", "a-task", "b-task"])
        self.assertEqual([x["name"] for x in s.snapshot()],
                         ["c-task", "a-task", "b-task"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
