# -*- coding: utf-8 -*-
"""my-service 示例定时任务：往日志里追加一行，演示 task 模式。

用法:
    python demo_task.py --note "demo heartbeat" --log ../../data/demo-task.log
"""
from __future__ import annotations

import argparse
import datetime
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser(description="my-service 示例定时任务")
    ap.add_argument("--note", default="demo heartbeat")
    ap.add_argument("--log", default="demo-task.log")
    args = ap.parse_args()

    log = Path(args.log)
    log.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().isoformat(timespec="seconds")
    with log.open("a", encoding="utf-8") as fh:
        fh.write(f"{stamp} {args.note}\n")
    print(f"task ok: {args.note} -> {log}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
