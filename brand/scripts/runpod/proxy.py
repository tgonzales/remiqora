"""A local stand-in for ACE-Step's API that forwards every request to a RunPod Serverless load-balancing endpoint.

The studio talks to http://127.0.0.1:<port> as it would to a local ACE-Step. Each request goes to the endpoint's
URL with the RunPod API key added. /health is answered here, from the answer the remote ACE-Step gave when it
was warmed up: the studio checks it all the time, and forwarding it would keep a GPU worker awake for nothing.

When the endpoint has scaled to zero, RunPod holds a request for up to two minutes while a worker starts and then
answers "no workers available"; such a request never reached ACE-Step, so it is sent again until a worker takes
it (up to COLD_START_SECONDS).
"""
from __future__ import annotations

import http.client
import json
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable

HOP_BY_HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers",
              "transfer-encoding", "upgrade", "host", "authorization", "content-length",
              # RunPod's edge (Cloudflare) compresses for any client that asks, e.g. brotli for the browser's
              # header; the studio's backend cannot decode brotli and would pass the raw bytes on. Without
              # this header http.client asks for identity, the plain body a local ACE-Step would send.
              "accept-encoding"}
COLD_START_SECONDS = 900
NO_WORKER = b"no workers available"


def log(message: str) -> None:
    print(f"[nix-serverless] {message}", file=sys.stderr, flush=True)


class Upstream:
    def __init__(self, base_url: str, api_key: str) -> None:
        parts = urllib.parse.urlsplit(base_url)
        self.host, self.prefix = parts.netloc, parts.path.rstrip("/")
        self.connection = http.client.HTTPConnection if parts.scheme == "http" else http.client.HTTPSConnection
        self.api_key = api_key
        self.last_used = time.monotonic()

    def send(self, method: str, path: str, headers: dict[str, str], body: bytes | None,
             timeout: float = 400) -> tuple[http.client.HTTPConnection, http.client.HTTPResponse]:
        conn = self.connection(self.host, timeout=timeout)
        conn.request(method, self.prefix + path, body=body,
                     headers={**headers, "Authorization": f"Bearer {self.api_key}", "Host": self.host})
        self.last_used = time.monotonic()
        return conn, conn.getresponse()

    def send_until_served(self, method: str, path: str, headers: dict[str, str], body: bytes | None,
                          deadline_s: float = COLD_START_SECONDS, on_wait: Callable[[str], None] = log):
        """Sends, and sends again while RunPod says no worker took it; returns (conn, response, cached_body)."""
        deadline = time.monotonic() + deadline_s
        while True:
            try:
                conn, response = self.send(method, path, headers, body)
            except OSError as exc:  # never reached the load balancer: safe to send again
                if time.monotonic() > deadline:
                    raise
                on_wait(f"RunPod not reachable ({exc}); trying again")
                time.sleep(5)
                continue
            if response.status < 500:
                return conn, response, None
            data = response.read()
            conn.close()
            if NO_WORKER not in data.lower() or time.monotonic() > deadline:
                return None, response, data
            on_wait("waiting for a GPU worker to start (cold start)")
            time.sleep(5)


def warm(upstream: Upstream, deadline_s: float = COLD_START_SECONDS) -> bytes:
    """Waits until the remote ACE-Step answers its /health with 200 and returns that answer."""
    deadline = time.monotonic() + deadline_s
    while True:
        conn, response, data = upstream.send_until_served("GET", "/health", {}, None,
                                                         max(deadline - time.monotonic(), 1))
        if data is None and conn is not None:
            data = response.read()
            conn.close()
        data = data or b""
        if response.status == 200:
            return data
        if time.monotonic() > deadline:
            raise TimeoutError(f"ACE-Step on RunPod did not get ready: HTTP {response.status} {data[:200]!r}")
        log(f"ACE-Step is loading (HTTP {response.status})")
        time.sleep(5)


def serve(upstream: Upstream, port: int, health_body: bytes, stop: threading.Event | None = None) -> None:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"  # the reply ends when the connection closes: no length needed for streams

        def _forward(self) -> None:
            if self.command == "GET" and urllib.parse.urlsplit(self.path).path == "/health":
                self._reply(200, health_body, "application/json")
                return
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else None
            headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP_BY_HOP}
            try:
                conn, response, data = upstream.send_until_served(self.command, self.path, headers, body)
            except Exception as exc:  # noqa: BLE001
                self._reply(502, json.dumps({"error": f"RunPod: {exc}"}).encode(), "application/json")
                return
            self.send_response(response.status)
            for key, value in response.getheaders():
                if key.lower() not in HOP_BY_HOP or (key.lower() == "content-length" and data is None):
                    self.send_header(key, value)
            self.end_headers()
            if data is not None:
                self.wfile.write(data)
                return
            try:
                while chunk := response.read(65536):
                    self.wfile.write(chunk)
            finally:
                if conn is not None:
                    conn.close()

        def _reply(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = _forward

        def log_message(self, *_args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    if stop is not None:
        threading.Thread(target=lambda: (stop.wait(), server.shutdown()), daemon=True).start()
    log(f"ACE-Step on RunPod answers on http://127.0.0.1:{port}")
    server.serve_forever()
