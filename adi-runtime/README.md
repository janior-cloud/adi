# adi-runtime

The server half of ADI: a localhost HTTP + SSE surface, three degradation primitives,
and no dependencies outside the Python standard library.

**Status: working.** `python -m unittest discover -s tests` → 18 tests, all passing (3.10+; also runs on 3.9).
Smoke-tested end to end against `adi-sdk`.

## Run it

```bash
cd adi-runtime
python3.11 -m adi_runtime --port 8899 --state ./state.json
```

```
adi-runtime 0.1.0 on http://127.0.0.1:8899 (providers: echo)
```

Providers: `echo` (offline smoke target), `command:<argv>` (any local CLI, `{prompt}` is
substituted), `http:<url>` (OpenAI-compatible endpoint). Chain them in code; the CLI takes one.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | liveness, boot id, which providers the chain will try |
| GET | `/state` | host-supplied truth — the only source proactive lines may cite |
| POST | `/chat` | `{"message": "..."}` → `text/event-stream`: `meta`, `delta`×N, `final` |
| POST | `/say` | `{"text": "...", "reason": "..."}` → push into the speech queue |
| GET | `/say/poll?after=N&boot=X` | new lines; `reset: true` when the queue rebooted |
| GET | `/proactive?force=1` | `{"text": ...}` or `{"text": null, "reason": "cooldown"}` |

Every path also answers under `/api/`, because embedded shells assume that prefix.

## The three pieces

**`fallback.Chain`** — providers tried in order, per call. The answer records who replied and
who failed, so "degraded" is a fact you can log, not a feeling. When every provider raises,
`AllProvidersFailed` carries each reason. A host is expected to put a local provider last in
the list and never let the chain be empty.

**`channels.SpeechQueue`** — a bounded in-memory queue plus an append-only JSONL trail. Any
process can push; the device polls. Two details exist because they broke in production:

- `boot` id — after a restart ids start over at 1, so a client holding `cursor=7` would skip
  everything until the queue passed 7 again. Clients compare `boot` and reset.
- The queue is capped at 50 lines. A speaker that stops polling must not grow memory forever.

**`proactive.ProactiveGate`** — refuses to speak without facts. It reads numbers from
`/state` on the call itself, rotates through candidates that cite them, and enforces a
cooldown persisted to disk. No state means no line, which is the correct failure mode.

## Protocol gotcha worth repeating

`delta` frames are slices of the reply; `final` carries the whole thing. A client that
concatenates both gets the answer twice. This SDK had exactly that bug, caught by an
end-to-end test — the fix is to stop yielding once `final` arrives if any `delta` was seen.

## Test

```bash
python3.11 -m unittest discover -s tests -t . -v
```

The HTTP tests start a real server on an ephemeral port and read the SSE stream over a
socket, because the bugs worth catching (framing, cursor reset, duplicate payloads) are on
the wire, not in the classes.

## License

AGPL-3.0. The SDK in `../adi-sdk` is Apache-2.0 — build commercial products against it
freely; improvements to this runtime go back upstream.
