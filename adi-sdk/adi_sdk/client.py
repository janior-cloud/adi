"""ADI SDK - a minimal client for a local ADI runtime.

Standard library only. The runtime listens on http://127.0.0.1:8899 by default.

    from adi_sdk import ADI

    adi = ADI()                        # or ADI("http://127.0.0.1:8899")
    for chunk in adi.chat("what changed today?"):
        print(chunk, end="", flush=True)     # streaming
    adi.say("backup finished")               # have it speak a line locally
    if line := adi.proactive():              # usually None - by design
        print(line)
    print(adi.state()["memory"])             # real numbers, not estimates
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Iterator

__all__ = ["ADI", "ADIError"]
__version__ = "0.1.0"

# A localhost runtime must never be routed through the machine's HTTP proxy:
# a VPN or proxy app in a bad state then turns every local call into a 503,
# which looks exactly like "the runtime is down".
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class ADIError(RuntimeError):
    """The runtime is unreachable, or it returned an error event."""


class ADI:
    def __init__(self, base: str = "http://127.0.0.1:8899", timeout: float = 180.0):
        self.base = base.rstrip("/")
        self.timeout = timeout

    # ---- 基础 ----

    def _post(self, path: str, payload: dict) -> dict:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base}{path}", data=body,
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with _OPENER.open(req, timeout=self.timeout) as resp:
                return json.loads(resp.read() or b"{}")
        except urllib.error.URLError as exc:
            raise ADIError(f"{path} unreachable: {exc}") from exc

    def _get(self, path: str) -> dict:
        try:
            with _OPENER.open(f"{self.base}{path}", timeout=self.timeout) as resp:
                return json.loads(resp.read() or b"{}")
        except urllib.error.URLError as exc:
            raise ADIError(f"{path} unreachable: {exc}") from exc

    # ---- 能力 ----

    def health(self) -> dict:
        return self._get("/api/health")

    def state(self) -> dict:
        """真实状态：会话数、消息数、待办、通道……全部来自运行时，不是估算。"""
        return self._get("/api/state")

    def chat(self, message: str) -> Iterator[str]:
        """流式对话。逐段 yield 文本；出错抛 ADIError。"""
        body = json.dumps({"message": message}, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base}/api/chat", data=body,
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            resp = _OPENER.open(req, timeout=self.timeout)
        except urllib.error.URLError as exc:
            raise ADIError(f"/api/chat unreachable: {exc}") from exc
        streamed = False
        kind = ""
        with resp:
            for raw in resp:
                line = raw.decode("utf-8", "replace").strip()
                if line.startswith("event:"):
                    kind = line[6:].strip()
                    continue
                if not line.startswith("data:"):
                    continue
                try:
                    event = json.loads(line[5:].strip())
                except json.JSONDecodeError:
                    continue
                # Standard SSE carries the type on the `event:` line; some
                # servers also repeat it inside the JSON. Accept both.
                event.setdefault("event", kind)
                if event.get("event") == "delta":
                    text = event.get("text") or event.get("delta") or ""
                    if text:
                        streamed = True
                        yield text
                elif event.get("event") == "final":
                    # delta 已经是全文的切片，final 里再给一次全文——已经流式输出过就不要再吐，
                    # 否则整句会重复两遍（实测踩到过）。
                    reply = event.get("reply")
                    if reply and not streamed:
                        yield reply
                    return
                elif event.get("event") == "error":
                    raise ADIError(str(event.get("error") or "chat failed"))

    def ask(self, message: str) -> str:
        """非流式：一次拿完整回答。"""
        return "".join(self.chat(message)).strip()

    def say(self, text: str, reason: str = "sdk") -> dict:
        """把一句话塞进它的嘴：运行时会用本地神经语音念出来。"""
        return self._post("/api/say", {"text": text, "reason": reason})

    def proactive(self, force: bool = False) -> str | None:
        """问它现在有什么值得主动说的。没依据时返回 None——这是设计，不是故障。"""
        data = self._get(f"/api/proactive{'?force=1' if force else ''}")
        return (data or {}).get("text") or None
