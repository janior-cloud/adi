"""A stand-in for the remote brain, so the demo can really kill it.

This is not a mock inside the runtime — it is a separate process listening on a
real socket. `offline_demo.py` terminates it, which produces a real
ECONNREFUSED on the next call. That is the whole point: the degradation you see
in the recording is the same code path that runs when a real cloud endpoint dies.

    python3 cloud_brain.py --port 9100 --delay 0.8
"""
from __future__ import annotations

import argparse
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DELAY = 0.8


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        pass

    def do_GET(self) -> None:  # noqa: N802
        body = json.dumps({"status": "ok", "role": "remote-brain"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            payload = {}
        prompt = ""
        for msg in reversed(payload.get("messages") or []):
            if msg.get("role") == "user":
                prompt = str(msg.get("content") or "")
                break
        time.sleep(DELAY)  # a network round trip you can see
        reply = (f"[remote] Heard you: \u201c{prompt[:60]}\u201d. "
                 f"The remote model answered \u2014 this path costs money and needs a network.")
        body = json.dumps({"choices": [{"message": {"role": "assistant", "content": reply}}],
                           "model": "stand-in-remote"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    global DELAY
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9100)
    ap.add_argument("--delay", type=float, default=DELAY)
    args = ap.parse_args()
    DELAY = args.delay
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"remote brain on 127.0.0.1:{args.port} (delay {args.delay}s)", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
