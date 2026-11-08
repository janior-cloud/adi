# ADI — Agent–Device Interface

**Local-first runtime for agents that talk, see, and act on real devices.**
No cloud required. Cloud is an optional accelerator, never the substrate.

`pre-alpha` — the skeleton is real and runs on macOS; the public API is not frozen yet.

---

## Why

Every assistant you use today dies when the network does. The model, the voice, the memory,
the UI — all rented from one vendor, all unavailable the moment a single HTTP call fails.

ADI is built the other way around: **every layer has a local fallback, and the system stays
alive when any one of them is removed.**

That constraint produces software with different properties:

| Property | What it means in practice |
|---|---|
| Degradable | Voice, LLM, and UI each fall back locally instead of erroring out |
| Replaceable | Model, voice, and vendor are swappable parts, not identity |
| Private | Memory lives on your machine; nothing is uploaded by default |
| Device-aware | Agents can point at a real object and explain it, not just chat |
| Restrained | Proactive speech is gated by evidence, not by a timer |

## Architecture

```
┌─────────────────────────────────────────────────────┐
│  adi-app      macOS shell (AppKit + WKWebView)      │  licensed AGPL-3.0
├─────────────────────────────────────────────────────┤
│  adi-runtime  orchestration, fallback chains,       │  licensed AGPL-3.0
│               channels, proactive gating            │
├─────────────────────────────────────────────────────┤
│  adi-sdk      Python / TypeScript client            │  licensed Apache-2.0
├─────────────────────────────────────────────────────┤
│  docs     documentation site (GitHub Pages)     │  CC BY 4.0
└─────────────────────────────────────────────────────┘
```

The runtime speaks plain HTTP + SSE on localhost. Anything that can POST JSON can drive it:
a desktop shell, a phone app, a cron job, another agent.

## Design rules

1. **No cloud required.** Every feature must have a local path. A cloud call is a
   performance decision, never a correctness dependency.
2. **Every layer degradable.** If the neural voice server is gone, fall back to the system
   voice. If the remote brain is gone, fall back to the local one. Never go silent.
3. **Names are not identities.** Swapping a model or a voice must not change who the system
   is. Identity lives in memory and permissions, not in a vendor string.
4. **Speak only when it changes your next action.** A proactive line must cite a real number
   from the state API, or it is not spoken at all.
5. **Public and private are physically separated.** This repository is the public shell.
   The private system it was extracted from is never published, mirrored, or bundled.

## Status

| Part | State |
|---|---|
| `adi-sdk` | client for `/chat`, `/say`, `/proactive`, `/state` — working |
| `adi-runtime` | HTTP + SSE, fallback chain, speech queue, proactive gate, built-in page — working, 19 tests passing |
| `adi-app` | macOS shell: single instance, supervises the runtime, speaks the queue — builds and runs |
| `demo` | a real offline failover, recorded: remote process killed, local model answers |
| `docs` | live at https://janior-cloud.github.io/adi/ |

Measured on a 2021 MacBook Pro, macOS 27, local models only:

- voice: English neural voice model 114 MB, Chinese 325 MB, warm end-to-end 2.1 s per sentence
- brain: local 35B MoE resident, first token under 1.2 s on the voice path
- chat: local-first, remote brain optional, 2.4–13 s per turn when the remote path is used

## License

| Path | License | Why |
|---|---|---|
| `adi-sdk/` | Apache-2.0 | usable in commercial products |
| `adi-runtime/`, `adi-app/` | AGPL-3.0 | improvements stay open |
| `docs/` | CC BY 4.0 | docs are meant to spread |

`ADI` and the diamond mark are trademarks of the project. Forks are welcome; forks that ship
as a competing hosted service are not.

## Contributing

The most useful contribution right now is a **fallback you can prove**: pick one layer, pull
the plug on it, and show the system still answering. Open an issue with the command and the
output. Reproducible degradation reports are worth more than feature requests.
