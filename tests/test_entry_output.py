r"""my-service · tests/test_entry_output.py — detached 入口脚本输出的可见性回归

背景（线上真事）：官方 start.ps1 凭 `%LOCALAPPDATA%\<vendor>\<app>.pid` 里一个
**已被系统回收复用**的 pid 就宣布 "SomeService is already running" 然后 exit 0——
真进程根本没起。框架这边因为入口输出一律 DEVNULL，只能记下"健康检查超时(60s)"，
真实原因彻底丢失，排查绕了远路。

本测试锁住修复后的行为：
  - detached 型入口的输出被捕获到 data\\entry-out\\<svc>.out
  - 脚本退场且健康未就绪 → 登记簿 note 里带上 rc 与脚本输出尾部
  - 健康就绪 → 捕获文件被清理，note 里不掺失败线索
  - managed / on_demand 型行为不变（输出仍丢弃，不产生捕获文件）

纯标准库 unittest，tempfile 隔离，不触碰真实 reg/ 与 data/。
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from core import audit, runner                          # noqa: E402
from core.scanner import Scanner                        # noqa: E402
from core.state import Manager                          # noqa: E402

# 桩入口脚本：喊一句"已在运行"然后立刻退场（模拟 pid 复用导致的静默失败）
_FAKE_ALREADY = (
    "import sys\n"
    "print('[WARNING] SomeService is already running (PID: 13368)')\n"
    "sys.exit(0)\n"
)

# 桩入口脚本：什么都不喊就退场（输出为空，note 不该被空串污染）
_FAKE_SILENT = "import sys\nsys.exit(0)\n"


class _Sandbox:
    """临时沙箱：注册目录 + 桩入口 + 独立的框架运行态目录。"""

    def __init__(self, entry_body: str = _FAKE_ALREADY):
        self.root = Path(tempfile.mkdtemp(prefix="mentry-test-"))
        self.reg = self.root / "reg"
        self.reg.mkdir()
        self.runtime = self.root / "data"
        self.runtime.mkdir()
        self.logs = self.root / "logs"
        self.logs.mkdir()
        (self.root / "entry.py").write_text(entry_body, encoding="utf-8")

    def put(self, name: str = "fake", **kw) -> Path:
        # 端口取一个几乎不可能被监听的号，保证健康探测必然失败
        data = {
            "path": str(self.root),
            "mode": "managed",
            "start": {"entry": "entry.py", "detach": True},
            "stop": {"script": "entry.py"},
            "identity": {"port": 1, "timeout_sec": 1},
        }
        data.update(kw)
        p = self.reg / f"{name}.json"
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return p

    def manager(self) -> Manager:
        runner.set_runtime_dir(self.runtime)
        return Manager(Scanner(self.reg))

    def events(self) -> list[dict]:
        path = audit.current_path()
        if path is None or not path.is_file():
            return []
        return [json.loads(ln) for ln in
                path.read_text(encoding="utf-8").splitlines() if ln.strip()]


class EntryOutputTest(unittest.TestCase):
    def setUp(self):
        self.sandboxes: list[_Sandbox] = []

    def tearDown(self):
        import shutil
        for sb in self.sandboxes:
            shutil.rmtree(sb.root, ignore_errors=True)
        # audit 全局态复位，避免污染其他测试模块
        audit._path = None  # noqa: SLF001

    def _sandbox(self, **kw) -> _Sandbox:
        sb = _Sandbox(**kw)
        self.sandboxes.append(sb)
        audit.init(sb.logs)
        return sb

    def _drive_to_terminal(self, mgr: Manager, name: str = "fake",
                           ticks: int = 12) -> None:
        """推进 monitor 若干轮，直到 starting 收敛成终态。

        health.timeout_sec=1 是 Scanner 的取值；探测失败在真机上可能要 1.5~2s
        才返回（连接拒绝的等待），故这里按 1 秒一轮推进、最多等约 12 轮。
        """
        for _ in range(ticks):
            mgr._tick()      # noqa: SLF001 - 测试直驱状态机
            if mgr.state[name]["status"] != "starting":
                return
            time.sleep(1.0)

    def test_captures_entry_output_when_health_never_ready(self):
        """入口秒退 + 健康未就绪 → note 里必须有脚本原话。"""
        sb = self._sandbox()
        sb.put()
        mgr = sb.manager()
        mgr.refresh()
        ok, _msg = mgr.start("fake")
        self.assertTrue(ok)

        self._drive_to_terminal(mgr)

        st = mgr.state["fake"]
        self.assertEqual(st["status"], "failed")
        self.assertIn("already running", st["error"])
        self.assertIn("13368", st["error"])

        # 登记簿里那条失败事件同样要带原话（面板/CLI 都读这里）
        fails = [e for e in sb.events() if e.get("svc") == "fake" and not e.get("ok")]
        self.assertTrue(fails, "应当留下一条失败事件")
        note = fails[-1].get("note", "")
        self.assertIn("already running", note)
        self.assertIn("rc=0", note)
        # note 长度受 audit.NOTE_MAX 约束，不能因为拼接而溢出
        self.assertLessEqual(len(note), audit.NOTE_MAX)

    def test_no_noise_when_entry_is_silent(self):
        """入口什么都没说 → 不得往 note 里塞空壳标记。"""
        sb = self._sandbox(entry_body=_FAKE_SILENT)
        sb.put()
        mgr = sb.manager()
        mgr.refresh()
        mgr.start("fake")

        self._drive_to_terminal(mgr)

        st = mgr.state["fake"]
        self.assertEqual(st["status"], "failed")
        self.assertNotIn("入口脚本", st["error"])

    def test_managed_mode_keeps_discarding_output(self):
        """非 detached 型不产生捕获文件（行为与改动前一致）。"""
        sb = self._sandbox(entry_body="import time\ntime.sleep(30)\n")
        sb.put(mode="managed",
               start={"entry": "entry.py", "detach": False},
               identity={"port": 1, "timeout_sec": 1})
        mgr = sb.manager()
        mgr.refresh()
        mgr.start("fake")
        try:
            self.assertFalse(
                runner.entry_output_path(mgr.specs["fake"]).exists(),
                "managed 型不该捕获入口输出")
        finally:
            mgr.stop("fake")


if __name__ == "__main__":
    unittest.main()
