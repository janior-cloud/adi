"""A queue any process can push speech into.

The device polls `since(cursor)` every couple of seconds and speaks whatever is
new. Two details matter and both were learned the hard way:

1. **Boot id.** After a restart the ids start over at 1, so a client holding
   cursor=7 would silently skip everything until the queue passed 7 again. The
   client compares `boot` and resets its cursor when it changes.
2. **The queue is bounded.** A speaker that never polls must not grow memory
   forever; old lines are dropped, and the JSONL trail keeps the history that
   matters for auditing anyway.
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import dataclass, asdict
from pathlib import Path

MAX_ITEMS = 50
MAX_TEXT = 600


@dataclass
class Line:
    id: int
    text: str
    reason: str
    at: float

    def as_dict(self) -> dict:
        return asdict(self)


class SpeechQueue:
    def __init__(self, trail: Path | None = None, max_items: int = MAX_ITEMS):
        self.boot = uuid.uuid4().hex[:8]
        self.trail = Path(trail) if trail else None
        self._max = max_items
        self._items: list[Line] = []
        self._last = 0
        self._lock = threading.Lock()

    def push(self, text: str, reason: str = "api") -> dict:
        text = (text or "").strip()[:MAX_TEXT]
        if not text:
            return {"ok": False, "error": "empty_text"}
        with self._lock:
            self._last += 1
            line = Line(id=self._last, text=text, reason=reason, at=time.time())
            self._items.append(line)
            if len(self._items) > self._max:
                del self._items[:-self._max]
        self._append_trail(line)
        return {"ok": True, "boot": self.boot, **line.as_dict()}

    def since(self, after: int = 0) -> list[dict]:
        with self._lock:
            items = [dict(id=i.id, text=i.text, reason=i.reason, at=i.at)
                     for i in self._items if i.id > int(after or 0)]
        return items

    def poll(self, after: int = 0, client_boot: str | None = None) -> dict:
        """The endpoint clients actually call. Resets the cursor on boot change."""
        reset = bool(client_boot) and client_boot != self.boot
        effective = 0 if reset else int(after or 0)
        return {"boot": self.boot, "cursor": self._last, "reset": reset,
                "items": self.since(effective)}

    def last_id(self) -> int:
        with self._lock:
            return self._last

    def _append_trail(self, line: Line) -> None:
        if not self.trail:
            return
        try:
            self.trail.parent.mkdir(parents=True, exist_ok=True)
            with self.trail.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(line.as_dict(), ensure_ascii=False) + "\n")
        except OSError:
            # An audit trail that fails must not stop the system from speaking.
            pass
