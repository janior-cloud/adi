# adi-runtime

Orchestration for a local-first agent: fallback chains, channel wiring, proactive gating.

**Status:** the private version runs this daily. The public extraction is in progress —
this directory is a placeholder until the first clean cut is reviewed.

What will land here:

- `fallback.py` — ordered provider chain with per-call degradation and ledger logging
- `channels.py` — a queue any process can push speech into, with boot-id cursor handling
- `proactive.py` — evidence-gated speech: a line is generated only from real state numbers
- `server.py` — localhost HTTP + SSE surface (`/chat`, `/say`, `/say/poll`, `/proactive`, `/state`)

Nothing here requires a cloud account. A missing remote provider is a degraded path, not an error.
