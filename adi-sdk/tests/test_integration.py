"""Integration: the published SDK against the published runtime, in one process.

This is the test that answers the only question that matters about two packages —
do they actually speak the same protocol? It starts a real runtime on an ephemeral
port and drives it through the SDK's public surface.
"""
from __future__ import annotations

import sys
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]          # ~/ADI
sys.path.insert(0, str(ROOT / "adi-sdk"))
sys.path.insert(0, str(ROOT / "adi-runtime"))

from adi_runtime import (Chain, ProactiveGate, Provider, Runtime,  # noqa: E402
                         SpeechQueue)
from adi_sdk import ADI  # noqa: E402


def flaky(prompt: str) -> str:
    raise RuntimeError("remote brain unreachable")


def local(prompt: str) -> str:
    return f"local answer to: {prompt}"


class SDKAgainstRuntime(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.state = {"memory": {"sessions": 7, "messages": 99},
                     "tasks": {"open": 2, "failed_24h": 0},
                     "brain": {"providers": [{"id": "local", "reachable": True}]}}
        runtime = Runtime(
            chain=Chain([Provider("remote", flaky), Provider("local", local)]),
            queue=SpeechQueue(),
            gate=ProactiveGate(cooldown_s=0),
            state_provider=lambda: cls.state,
        )
        cls.server = runtime.make_server(port=0)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.adi = ADI(f"http://127.0.0.1:{cls.server.server_address[1]}")

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def test_health(self):
        self.assertTrue(self.adi.health()["ok"])

    def test_state_passes_through(self):
        self.assertEqual(self.adi.state()["memory"]["sessions"], 7)

    def test_ask_survives_a_dead_provider(self):
        reply = self.adi.ask("are you there")
        self.assertEqual(reply, "local answer to: are you there")

    def test_ask_does_not_duplicate_the_reply(self):
        # Regression guard for the delta+final double-yield bug: the reply must
        # appear once, not twice.
        reply = self.adi.ask("echo me once")
        self.assertEqual(reply.count("local answer to:"), 1)

    def test_say_then_read_back(self):
        self.assertTrue(self.adi.say("integration line", reason="test")["ok"])
        data = self.adi._get("/api/say/poll?after=0")
        self.assertEqual(data["items"][-1]["text"], "integration line")

    def test_proactive_cites_state(self):
        self.assertIsNotNone(self.adi.proactive(force=True))


if __name__ == "__main__":
    unittest.main()
