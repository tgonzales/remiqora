"""The load balancer's health check: 204 (initializing) until ACE-Step answers its own /health, then 200.

RunPod's load balancer reads 200 as healthy, 204 as initializing and anything else as unhealthy. ACE-Step does
not answer at all while it downloads and loads its models, which on the first start takes many minutes, so this
answers for it.

    python health.py PORT ACE_HEALTH_URL
"""
from __future__ import annotations

import sys
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT, TARGET = int(sys.argv[1]), sys.argv[2]


def ace_ready() -> bool:
    try:
        with urllib.request.urlopen(TARGET, timeout=3) as response:
            return response.status == 200
    except Exception:
        return False


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        self.send_response(200 if ace_ready() else 204)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *_args: object) -> None:
        pass


ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
