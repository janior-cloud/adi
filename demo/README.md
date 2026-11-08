# Offline demo

The claim ADI makes is narrow and testable: **when the remote brain disappears, the
answer still comes.** This directory contains the run that proves it, and the code
that regenerates the recording on your own machine.

```
demo/
├── cloud_brain.py      a stand-in remote provider (real HTTP server on :9100)
├── offline_demo.py     drives the sequence, writes last_run.json
├── render_frames.py    screenshots each state with Chrome, assembles GIF/MP4
└── last_run.json       the data behind the recording published on the site
```

## What is real and what is a stand-in

| Real | Stand-in |
|---|---|
| The runtime, its fallback chain, the SSE stream | The remote endpoint is `cloud_brain.py`, not a paid API |
| The kill: `Popen.terminate()` on a separate process | — |
| The resulting `URLError` / connection-refused | — |
| The local answer: a real llama.cpp server on your machine | — |

The remote being a stand-in does not weaken the claim. What matters is that the
failure is a genuine socket failure at the OS level, produced the same way a dead
cloud endpoint produces it. A mock flag inside the runtime would prove nothing;
killing a process proves the fallback path actually runs.

## Run it

```bash
# 1. a local OpenAI-compatible model server is required
#    (llama.cpp: `llama-server -m model.gguf --port 8094 --api-key <key>`)
curl -s localhost:8094/v1/models -H "Authorization: Bearer $KEY"

# 2. the demo
python3 demo/offline_demo.py --local-port 8094 --local-model <model-name>

# 3. the recording (needs Chrome + ffmpeg)
python3 demo/render_frames.py --out docs/assets/demo.gif
```

`offline_demo.py` writes `last_run.json`; nothing on screen is invented — the
questions, providers, timings and replies all come from that file. Only the pacing
is slowed so a human can read it.

## Reading the output

```
▸ The network just died. Are you still there?
  ✗ remote  URLError
  ✓ local   2.02s
  degraded= true   the answer came from this machine
```

`degraded: true` is not a warning label added for the video — it is the field the
chain has always returned when a provider failed before another one answered.

## Gotchas worth knowing

- **A reasoning model needs `chat_template_kwargs: {"enable_thinking": false}`** on a
  local llama.cpp server, or it spends the whole token budget thinking and returns an
  empty string. The demo passes it through `http_json_provider(extras=...)`.
- **`python3 -m http.server`-style mocks hide the bug you care about.** If the failure
  is simulated in-process, the connection layer is never exercised.
- **Even pixel heights.** `libx264` with `yuv420p` rejects odd dimensions — use
  `scale=820:-2`, not `:-1`.
- **One screenshot per state, not per character.** Typing animation at 10fps meant
  229 Chrome launches (4.5 min) for a worse result than 19 keyframes held for the
  right durations.
