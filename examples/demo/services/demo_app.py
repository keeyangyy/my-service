# -*- coding: utf-8 -*-
"""my-service 示例服务：一个带健康端点的极小 HTTP 面板。

仅用标准库，供 examples/demo 演示"注册 → 启停 → 点开"的完整链路。

用法:
    python demo_app.py --port 33510 --name 示例面板 --color blue [--pidfile demo.pid]
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

COLORS = {"blue": "#60a5fa", "green": "#34d399", "amber": "#fbbf24"}

PAGE = """<!doctype html>
<meta charset="utf-8">
<title>{name}</title>
<style>
  :root {{ --bg:#14161c; --fg:#e6e9f2; --dim:#8a93a8; --line:#2b3140; }}
  body {{ margin:0; min-height:100vh; display:grid; place-items:center;
          background:var(--bg); color:var(--fg);
          font:15px/1.6 "Segoe UI","Microsoft YaHei",sans-serif; }}
  .card {{ background:#1b1f2a; border:1px solid var(--line); border-radius:14px;
           padding:32px 44px; text-align:center; min-width:260px; }}
  h1 {{ margin:0; font-size:22px; font-weight:600; }}
  .dot {{ display:inline-block; width:9px; height:9px; border-radius:50%;
          background:{color}; margin-right:9px; vertical-align:middle; }}
  p {{ color:var(--dim); margin:10px 0 0; font-size:13px; }}
</style>
<div class="card">
  <h1><span class="dot"></span>{name}</h1>
  <p>my-service 示例服务</p>
  <p>启动于 {started_at} · 端口 {port}</p>
</div>
"""


def make_handler(name: str, color: str, started_at: str, port: int):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802
            if self.path.startswith("/health"):
                payload = json.dumps({"ok": True, "service": name}).encode("utf-8")
                return self._send(200, payload, "application/json; charset=utf-8")
            html = PAGE.format(name=name, color=color, started_at=started_at,
                               port=port).encode("utf-8")
            return self._send(200, html, "text/html; charset=utf-8")

        def log_message(self, fmt, *args):  # noqa: A003 - 示例服务不写访问日志
            pass

    return Handler


def main() -> int:
    ap = argparse.ArgumentParser(description="my-service 示例服务")
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--name", default="示例服务")
    ap.add_argument("--color", default="blue")
    ap.add_argument("--pidfile", default="")
    args = ap.parse_args()

    if args.pidfile:
        with open(args.pidfile, "w", encoding="ascii") as fh:
            fh.write(str(os.getpid()))

    started = datetime.now().strftime("%H:%M:%S")
    httpd = ThreadingHTTPServer(
        ("127.0.0.1", args.port),
        make_handler(args.name, COLORS.get(args.color, args.color), started, args.port),
    )
    httpd.daemon_threads = True
    print(f"{args.name} listening on http://127.0.0.1:{args.port}", flush=True)
    httpd.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
