"""Evidence-gated proactive speech.

Most assistants that speak unprompted do it on a timer, which trains the user to
ignore them. This gate refuses to speak unless it can cite a number it read from
the state API *this* call. No facts, no line. A cooldown on top, so a busy state
cannot turn into a chatty one.

The host decides what counts as a fact; the gate only knows how to turn facts
into a sentence and how to stay quiet.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable

DEFAULT_COOLDOWN = 20 * 60

DEFAULT_TEMPLATES: dict[str, str] = {
    "failed_tasks": "There are {failed} tasks that failed in the last 24h. Want me to look at one?",
    "open_tasks": "{open} items are still open. I'll take the smallest one unless you say stop.",
    "providers": "Reachable channels right now: {providers}. Standing by.",
    "memory_scale": "Memory holds {sessions} conversations and {messages} messages. Ask and I'll search it.",
    "idle": "I'm here. Say the word, or give me something to take apart.",
}


class ProactiveGate:
    def __init__(self, state_file: Path | None = None,
                 cooldown_s: float = DEFAULT_COOLDOWN,
                 templates: dict[str, str] | None = None,
                 clock: Callable[[], float] = time.time):
        self.state_file = Path(state_file) if state_file else None
        self.cooldown_s = cooldown_s
        self.templates = {**DEFAULT_TEMPLATES, **(templates or {})}
        self._clock = clock
        self._rotation = 0

    # ---- facts ----

    @staticmethod
    def facts(state: dict | None) -> dict:
        """Only numbers that can be checked right now. Never inferred."""
        if not state:
            return {}
        memory = state.get("memory") or {}
        tasks = state.get("tasks") or {}
        providers = [p.get("id") for p in (state.get("brain") or {}).get("providers", [])
                     if p.get("reachable")]
        raw = {
            "sessions": memory.get("sessions"),
            "messages": memory.get("messages"),
            "open": tasks.get("open"),
            "failed": tasks.get("failed_24h"),
            "providers": providers,
        }
        return {k: v for k, v in raw.items() if v not in (None, [], "")}

    def _last_at(self) -> float:
        if not self.state_file:
            return 0.0
        try:
            return float(json.loads(self.state_file.read_text()).get("last_at") or 0)
        except (OSError, ValueError, TypeError):
            return 0.0

    def _record(self, text: str, reason: str) -> None:
        if not self.state_file:
            return
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            self.state_file.write_text(json.dumps(
                {"last_at": self._clock(), "reason": reason, "text": text},
                ensure_ascii=False))
        except OSError:
            pass

    # ---- decision ----

    def candidates(self, facts: dict) -> list[tuple[str, str]]:
        """Ordered by how much the line changes your next action."""
        out: list[tuple[str, str]] = []

        failed = int(facts.get("failed") or 0)
        if failed and "failed_tasks" in self.templates:
            out.append((self.templates["failed_tasks"].format(failed=failed), "failed_tasks"))

        open_items = int(facts.get("open") or 0)
        if open_items and "open_tasks" in self.templates:
            out.append((self.templates["open_tasks"].format(open=open_items), "open_tasks"))

        providers = facts.get("providers") or []
        if providers and "providers" in self.templates:
            out.append((self.templates["providers"].format(
                providers=" / ".join(providers)), "providers"))

        if facts.get("sessions") and facts.get("messages") and "memory_scale" in self.templates:
            out.append((self.templates["memory_scale"].format(
                sessions=facts["sessions"], messages=facts["messages"]), "memory_scale"))

        # The idle line is the only one that needs no number, so it is always last.
        if "idle" in self.templates:
            out.append((self.templates["idle"], "idle"))

        return out

    def next_line(self, state: dict | None, force: bool = False) -> tuple[str | None, str]:
        """Return (line, reason). None means: say nothing right now."""
        now = self._clock()
        if not force and now - self._last_at() < self.cooldown_s:
            return None, "cooldown"
        facts = self.facts(state)
        if not facts:
            return None, "no_facts"
        options = self.candidates(facts)
        if not options:
            return None, "no_facts"
        # Rotate deterministically: the same sequence of calls always produces
        # the same order. Keying this off the clock made tests flaky and made a
        # restart jump the rotation for no reason.
        text, reason = options[self._rotation % len(options)]
        self._rotation = (self._rotation + 1) % len(options)
        self._record(text, reason)
        return text, reason
