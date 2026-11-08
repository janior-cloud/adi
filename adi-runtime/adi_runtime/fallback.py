"""Ordered provider chain with per-call degradation.

The rule this module exists to enforce: a missing brain is a downgrade, not an
outage. Callers never see "the cloud failed"; they see which provider answered
and how many were tried before it did.

A provider is anything callable that takes the prompt and returns text:

    def local(prompt: str) -> str: ...

Wrap a subprocess, an HTTP endpoint, a local model server — the chain does not
care what is behind it, only that it either returns a string or raises.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable


class AllProvidersFailed(RuntimeError):
    """Every provider in the chain raised. Carries the per-provider reasons."""

    def __init__(self, attempts: list[dict]):
        self.attempts = attempts
        summary = "; ".join(f"{a['provider']}: {a['error']}" for a in attempts)
        super().__init__(f"all providers failed ({summary})")


@dataclass
class Provider:
    name: str
    call: Callable[[str], str]
    timeout_s: float = 120.0
    # A provider that is known-dead (binary missing, port closed) can be marked
    # so the chain skips it without paying the failure cost on every turn.
    enabled: bool = True


@dataclass
class Answer:
    text: str
    provider: str
    attempts: list[dict] = field(default_factory=list)
    secs: float = 0.0

    @property
    def degraded(self) -> bool:
        """True when at least one provider failed before this one answered."""
        return any(not a.get("ok") for a in self.attempts)

    def as_dict(self) -> dict:
        return {"text": self.text, "provider": self.provider,
                "degraded": self.degraded, "secs": self.secs,
                "attempts": self.attempts}


class Chain:
    """Try providers in order. First non-empty answer wins; failures are logged."""

    def __init__(self, providers: list[Provider], log: Callable[[dict], None] | None = None):
        self.providers = providers
        self._log = log

    def add(self, provider: Provider) -> "Chain":
        self.providers.append(provider)
        return self

    def names(self) -> list[str]:
        return [p.name for p in self.providers if p.enabled]

    def ask(self, prompt: str) -> Answer:
        attempts: list[dict] = []
        started = time.time()
        for provider in self.providers:
            if not provider.enabled:
                continue
            t0 = time.time()
            try:
                text = provider.call(prompt)
            except Exception as exc:  # noqa: BLE001 - any provider error means "try the next one"
                attempts.append({"provider": provider.name, "ok": False,
                                 "error": f"{type(exc).__name__}: {exc}"[:300],
                                 "secs": round(time.time() - t0, 3)})
                continue
            text = (text or "").strip()
            if not text:
                attempts.append({"provider": provider.name, "ok": False,
                                 "error": "empty reply", "secs": round(time.time() - t0, 3)})
                continue
            attempts.append({"provider": provider.name, "ok": True,
                             "secs": round(time.time() - t0, 3)})
            answer = Answer(text=text, provider=provider.name, attempts=attempts,
                            secs=round(time.time() - started, 3))
            if self._log:
                self._log({"event": "answer", "prompt_chars": len(prompt),
                           "provider": answer.provider, "degraded": answer.degraded,
                           "secs": answer.secs, "attempts": attempts})
            return answer
        if self._log:
            self._log({"event": "exhausted", "prompt_chars": len(prompt), "attempts": attempts})
        raise AllProvidersFailed(attempts)


# ---- ready-made providers (stdlib only) ----

def command_provider(name: str, argv: list[str], *, cwd: str | None = None,
                     clean_python_env: bool = True, timeout_s: float = 120.0) -> Provider:
    """Run a local CLI and use its stdout as the answer.

    `{prompt}` inside any argv element is replaced with the prompt, so a provider
    can be as simple as ["hermes", "chat", "-q", "{prompt}", "--oneshot"].
    """
    import os
    import subprocess

    def call(prompt: str) -> str:
        args = [a.replace("{prompt}", prompt) for a in argv]
        env = dict(os.environ)
        if clean_python_env:
            # A host's venv or PYTHONPATH must not leak into the child interpreter.
            for key in ("VIRTUAL_ENV", "PYTHONPATH", "PYTHONHOME"):
                env.pop(key, None)
        proc = subprocess.run(args, cwd=cwd, env=env, capture_output=True,
                              text=True, timeout=timeout_s)
        if proc.returncode != 0:
            raise RuntimeError(((proc.stderr or "").strip() or f"exit {proc.returncode}")[:300])
        return proc.stdout

    return Provider(name=name, call=call, timeout_s=timeout_s)


def http_json_provider(name: str, url: str, *, model: str | None = None,
                       api_key: str | None = None, timeout_s: float = 120.0) -> Provider:
    """Call an OpenAI-compatible /chat/completions endpoint over plain urllib.

    No SDK dependency on purpose: this is the "remote accelerator" slot, and it
    must stay the easiest thing in the system to delete.
    """
    import json
    import urllib.request

    def call(prompt: str) -> str:
        payload: dict = {"messages": [{"role": "user", "content": prompt}]}
        if model:
            payload["model"] = model
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                     headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            data = json.loads(resp.read() or b"{}")
        return ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""

    return Provider(name=name, call=call, timeout_s=timeout_s)


def echo_provider(prefix: str = "") -> Provider:
    """Offline smoke-test provider: proves the chain without needing a model."""
    return Provider(name="echo", call=lambda prompt: f"{prefix}{prompt}".strip())
