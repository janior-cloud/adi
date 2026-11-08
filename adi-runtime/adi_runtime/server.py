"""The localhost surface: HTTP + SSE, standard library only.

Endpoints (all JSON unless noted):

    GET  /health              liveness + which providers the chain will try
    GET  /state               host-supplied truth (numbers the proactive gate cites)
    POST /chat                {"message": "..."} → text/event-stream of delta frames + final
    POST /say                 {"text": "...", "reason": "..."} → push a line into the speech queue
    GET  /say/poll?after=N&boot=X  → new lines; `reset` is true when the queue rebooted
    GET  /proactive?force=1   → {"text": ...} or {"text": null, "reason": "cooldown"}

Why SSE instead of a chat socket: the device speaks as tokens arrive, and a
plain event stream survives every proxy, sleep/wake cycle and restart we tried.
The client must dedupe: `delta` frames are slices, `final` carries the whole
reply. Concatenating both is the most common integration bug.
"""
from __future__ import annotations

import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable
from urllib.parse import parse_qs, urlparse

from .channels import SpeechQueue
from .fallback import AllProvidersFailed, Chain
from .proactive import ProactiveGate
from .ui import PAGE

CHUNK = 18  # characters per delta frame — small enough to feel live, big enough to stay cheap


class Runtime:
    def __init__(self, chain: Chain, queue: SpeechQueue, gate: ProactiveGate,
                 state_provider: Callable[[], dict] | None = None,
                 name: str = "adi", version: str = "0.1.0", preamble: str = ""):
        self.chain = chain
        self.queue = queue
        self.gate = gate
        self.state_provider = state_provider or (lambda: {})
        self.name = name
        self.version = version
        self.preamble = preamble
        self.started = time.time()

    def health(self) -> dict:
        return {"ok": True, "name": self.name, "version": self.version,
                "boot": self.queue.boot, "uptime_s": round(time.time() - self.started, 1),
                "providers": self.chain.names(), "queue_last_id": self.queue.last_id()}

    def chat(self, message: str) -> dict:
        # Without this a local model happily announces that it runs in a cloud
        # data centre, because that is what its training data says agents do.
        prompt = f"{self.preamble}\n\n{message}" if self.preamble else message
        return self.chain.ask(prompt).as_dict()

    def chunks(self, text: str) -> list[str]:
        return [text[i:i + CHUNK] for i in range(0, len(text), CHUNK)] or [""]

    def make_server(self, host: str = "127.0.0.1", port: int = 8899) -> ThreadingHTTPServer:
        runtime = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, format: str, *args) -> None:  # noqa: A002 - signature is fixed by the base class
                pass

            # ---- helpers ----

            def _send(self, code: int, payload: dict) -> None:
                body = json.dumps(payload, ensure_ascii=False).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def _body(self) -> dict:
                length = int(self.headers.get("Content-Length") or 0)
                if not length:
                    return {}
                try:
                    return json.loads(self.rfile.read(length) or b"{}")
                except (ValueError, TypeError):
                    return {}

            def _query(self) -> dict:
                return {k: v[-1] for k, v in parse_qs(urlparse(self.path).query).items()}

            def _path(self) -> str:
                # Both /chat and /api/chat are accepted: the /api prefix is what
                # embedded shells tend to assume, and rejecting it would be a
                # gratuitous compatibility break for no benefit.
                path = urlparse(self.path).path
                return path[4:] if path.startswith("/api/") else path

            # ---- routes ----

            def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
                path = self._path()
                if path in ("/", "/ui", "/index.html"):
                    body = PAGE.encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(body)
                    return
                if path == "/health":
                    return self._send(200, runtime.health())
                if path == "/state":
                    try:
                        return self._send(200, runtime.state_provider() or {})
                    except Exception as exc:  # noqa: BLE001
                        return self._send(500, {"ok": False, "error": str(exc)[:300]})
                if path == "/say/poll":
                    q = self._query()
                    return self._send(200, runtime.queue.poll(
                        after=int(q.get("after") or 0), client_boot=q.get("boot")))
                if path == "/proactive":
                    q = self._query()
                    force = q.get("force") in ("1", "true", "yes")
                    try:
                        text, reason = runtime.gate.next_line(runtime.state_provider(), force=force)
                    except Exception as exc:  # noqa: BLE001
                        return self._send(500, {"ok": False, "error": str(exc)[:300]})
                    return self._send(200, {"text": text, "reason": reason})
                return self._send(404, {"ok": False, "error": "not_found", "path": path})

            def do_POST(self) -> None:  # noqa: N802
                path = self._path()
                if path == "/say":
                    body = self._body()
                    return self._send(200, runtime.queue.push(
                        body.get("text") or "", body.get("reason") or "api"))
                if path == "/chat":
                    return self._chat(self._body().get("message") or "")
                return self._send(404, {"ok": False, "error": "not_found", "path": path})

            def _chat(self, message: str) -> None:
                message = (message or "").strip()
                if not message:
                    return self._send(400, {"ok": False, "error": "empty_message"})
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Connection", "close")
                self.end_headers()

                def frame(event: str, payload: dict) -> None:
                    # The event name goes in both places on purpose: the SSE
                    # `event:` line is the standard, and consumers that only
                    # parse `data:` JSON still get the frame type.
                    body = json.dumps({"event": event, **payload}, ensure_ascii=False)
                    self.wfile.write(f"event: {event}\ndata: {body}\n\n".encode())
                    self.wfile.flush()

                try:
                    answer = runtime.chat(message)
                except AllProvidersFailed as exc:
                    frame("error", {"error": "all_providers_failed", "attempts": exc.attempts})
                    return
                except Exception as exc:  # noqa: BLE001
                    frame("error", {"error": f"{type(exc).__name__}: {exc}"[:300]})
                    return

                frame("meta", {"provider": answer["provider"], "degraded": answer["degraded"],
                               "attempts": answer["attempts"]})
                for piece in runtime.chunks(answer["text"]):
                    frame("delta", {"text": piece})
                frame("final", {"reply": answer["text"], "provider": answer["provider"],
                                "degraded": answer["degraded"], "secs": answer["secs"]})

        return ThreadingHTTPServer((host, port), Handler)

    def serve(self, host: str = "127.0.0.1", port: int = 8899) -> None:
        server = self.make_server(host, port)
        print(f"adi-runtime {self.version} on http://{host}:{port} "
              f"(providers: {', '.join(self.chain.names()) or 'none'})", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
