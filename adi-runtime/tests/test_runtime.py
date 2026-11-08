"""End-to-end and unit tests. Standard library only: python -m unittest discover -s tests

The HTTP tests start a real server on an ephemeral port and speak to it over a
real socket, because the bugs worth catching here (SSE framing, cursor reset,
dual delta/final payloads) live in the wire format, not in the classes.
"""
from __future__ import annotations

import json
import sys
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from adi_runtime import (AllProvidersFailed, Chain, ProactiveGate, Provider,  # noqa: E402
                         Runtime, SpeechQueue)


def boom(prompt: str) -> str:
    raise RuntimeError("provider down")


class ChainTests(unittest.TestCase):
    def test_falls_through_to_next_provider(self):
        chain = Chain([Provider("dead", boom), Provider("echo", lambda p: f"ok:{p}")])
        answer = chain.ask("hi")
        self.assertEqual(answer.provider, "echo")
        self.assertEqual(answer.text, "ok:hi")
        self.assertTrue(answer.degraded)
        self.assertEqual(len(answer.attempts), 2)

    def test_empty_reply_is_a_failure_not_an_answer(self):
        chain = Chain([Provider("blank", lambda p: "   "), Provider("echo", lambda p: "real")])
        self.assertEqual(chain.ask("x").text, "real")

    def test_all_failed_raises_with_reasons(self):
        chain = Chain([Provider("a", boom), Provider("b", boom)])
        with self.assertRaises(AllProvidersFailed) as ctx:
            chain.ask("x")
        self.assertEqual(len(ctx.exception.attempts), 2)
        self.assertIn("provider down", str(ctx.exception))

    def test_disabled_provider_is_skipped(self):
        chain = Chain([Provider("off", boom, enabled=False), Provider("echo", lambda p: "on")])
        answer = chain.ask("x")
        self.assertEqual(answer.provider, "echo")
        self.assertFalse(answer.degraded)


class QueueTests(unittest.TestCase):
    def test_push_and_since(self):
        q = SpeechQueue()
        q.push("one")
        q.push("two")
        self.assertEqual([i["text"] for i in q.since(1)], ["two"])

    def test_boot_change_resets_cursor(self):
        q = SpeechQueue()
        q.push("hello")
        poll = q.poll(after=99, client_boot="stale")
        self.assertTrue(poll["reset"])
        self.assertEqual(len(poll["items"]), 1)

    def test_empty_text_rejected(self):
        self.assertFalse(SpeechQueue().push("   ")["ok"])

    def test_trail_is_written(self):
        tmp = Path(__file__).resolve().parent / "_trail.jsonl"
        try:
            SpeechQueue(trail=tmp).push("audit me", reason="test")
            self.assertIn("audit me", tmp.read_text())
        finally:
            tmp.unlink(missing_ok=True)


class GateTests(unittest.TestCase):
    def state(self, **kw):
        base = {"memory": {"sessions": 12, "messages": 340},
                "tasks": {"open": 3, "failed_24h": 1},
                "brain": {"providers": [{"id": "local", "reachable": True},
                                        {"id": "remote", "reachable": False}]}}
        base.update(kw)
        return base

    def test_no_state_means_no_line(self):
        text, reason = ProactiveGate().next_line(None)
        self.assertIsNone(text)
        self.assertEqual(reason, "no_facts")

    def test_line_is_deterministic_and_ordered(self):
        gate = ProactiveGate(cooldown_s=0)
        lines = [gate.next_line(self.state()) for _ in range(5)]
        self.assertEqual([r for _, r in lines][:2], ["failed_tasks", "open_tasks"],
                         "highest-signal candidate must come first")
        self.assertTrue(all(t for t, _ in lines))

    def test_numeric_candidates_cite_a_number(self):
        gate = ProactiveGate(cooldown_s=0)
        for text, reason in gate.candidates(gate.facts(self.state())):
            if reason in ("failed_tasks", "open_tasks", "memory_scale"):
                self.assertTrue(any(ch.isdigit() for ch in text), f"{reason} lost its number")

    def test_cooldown_silences(self):
        gate = ProactiveGate(cooldown_s=600, clock=lambda: 1000.0)
        tmp = Path(__file__).resolve().parent / "_proactive.json"
        gate.state_file = tmp
        try:
            self.assertIsNotNone(gate.next_line(self.state())[0])
            text, reason = gate.next_line(self.state())
            self.assertIsNone(text)
            self.assertEqual(reason, "cooldown")
        finally:
            tmp.unlink(missing_ok=True)


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        chain = Chain([Provider("dead", boom), Provider("echo", lambda p: f"echo {p}")])
        cls.rt = Runtime(chain=chain, queue=SpeechQueue(), gate=ProactiveGate(cooldown_s=0),
                         state_provider=lambda: {"memory": {"sessions": 2, "messages": 5},
                                                 "tasks": {"open": 1, "failed_24h": 0},
                                                 "brain": {"providers": [{"id": "echo", "reachable": True}]}})
        cls.server = cls.rt.make_server(port=0)
        cls.port = cls.server.server_address[1]
        cls.base = f"http://127.0.0.1:{cls.port}"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=10) as r:
            return json.loads(r.read())

    def post(self, path, payload):
        req = urllib.request.Request(self.base + path, method="POST",
                                     data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())

    def test_health_lists_providers(self):
        health = self.get("/health")
        self.assertTrue(health["ok"])
        self.assertEqual(health["providers"], ["dead", "echo"])

    def test_ui_is_served_at_root(self):
        with urllib.request.urlopen(self.base + "/", timeout=10) as r:
            self.assertEqual(r.status, 200)
            self.assertIn("text/html", r.headers.get("Content-Type", ""))
            self.assertIn("ADI", r.read().decode()[:500])

    def test_state_is_host_supplied(self):
        self.assertEqual(self.get("/state")["memory"]["sessions"], 2)

    def test_chat_streams_deltas_that_rebuild_the_final_reply(self):
        req = urllib.request.Request(self.base + "/chat", method="POST",
                                     data=json.dumps({"message": "a prompt long enough to produce more than one frame"}).encode(),
                                     headers={"Content-Type": "application/json"})
        events, pieces, final = [], [], None
        with urllib.request.urlopen(req, timeout=10) as r:
            for raw in r:
                line = raw.decode().strip()
                if line.startswith("event:"):
                    events.append(line.split(":", 1)[1].strip())
                elif line.startswith("data:"):
                    payload = json.loads(line[5:].strip())
                    if events and events[-1] == "delta":
                        pieces.append(payload["text"])
                    if events and events[-1] == "final":
                        final = payload
        self.assertEqual(events[0], "meta")
        self.assertEqual(events[-1], "final")
        assert final is not None
        self.assertGreater(len(pieces), 1, "reply should arrive in several frames")
        self.assertEqual("".join(pieces), final["reply"], "deltas must rebuild the final text")
        self.assertTrue(final["degraded"], "the dead provider must be reported, not hidden")

    def test_say_then_poll_roundtrip(self):
        self.post("/say", {"text": "speak this", "reason": "test"})
        poll = self.get("/say/poll?after=0")
        self.assertEqual(poll["items"][-1]["text"], "speak this")
        self.assertEqual(poll["items"][-1]["reason"], "test")

    def test_proactive_endpoint_answers(self):
        data = self.get("/proactive?force=1")
        self.assertIn("text", data)
        self.assertIsNotNone(data["text"])

    def test_unknown_route_404(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get("/nope")
        self.assertEqual(ctx.exception.code, 404)


if __name__ == "__main__":
    unittest.main()
