"""Offline demo: the same runtime, before and after the remote brain disappears.

Everything here is real. The remote provider is a separate process on a real
socket; `cut()` terminates it, so the next call fails with a genuine
connection-refused. The local provider is a real model server. Nothing is faked
in the middle — what you see in the recording is the code path that runs when a
cloud endpoint dies.

    python3 demo/offline_demo.py --local-port 8094 --local-model <name>

Writes demo/last_run.json, which render_gif.py turns into the recording.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import socket
import subprocess
import sys
import threading
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "adi-runtime"))
sys.path.insert(0, str(ROOT / "adi-sdk"))

from adi_runtime import (Chain, ProactiveGate, Runtime, SpeechQueue,  # noqa: E402
                         http_json_provider)
from adi_sdk import ADI  # noqa: E402

QUESTIONS = [
    "备份跑完了吗？",                    # online: remote answers
    "网线拔了。你现在还在吗？",   # offline: must fall back to local
    "网恢复了。刚才那句是谁答的？",  # online again
]


def port_open(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


def wait_port(port: int, up: bool, timeout: float = 15.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if port_open(port) == up:
            return True
        time.sleep(0.1)
    return False


def local_key() -> str:
    """Key for the local model server, from the environment only.

    Never read from a dotfile inside this repo: a demo that needs a private
    secret path is not a demo anyone can run.
    """
    for name in ("ADI_LOCAL_KEY", "LLAMA_API_KEY", "OPENAI_API_KEY"):
        if os.environ.get(name):
            return os.environ[name]
    return ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8899, help="where the runtime listens")
    ap.add_argument("--remote-port", type=int, default=9100)
    ap.add_argument("--local-port", type=int, default=8094)
    ap.add_argument("--local-model", default="qwen36moe")
    ap.add_argument("--local-key", default="", help="defaults to $ADI_LOCAL_KEY / $LLAMA_API_KEY / $OPENAI_API_KEY")
    ap.add_argument("--out", default=str(ROOT / "demo" / "last_run.json"))
    args = ap.parse_args()

    steps: list[dict] = []
    t0 = time.time()

    def mark(kind: str, **kw) -> None:
        steps.append({"kind": kind, "t": round(time.time() - t0, 2), **kw})

    if not port_open(args.local_port):
        print(f"local model server is not listening on {args.local_port}. Start it first.", file=sys.stderr)
        return 2

    cloud = subprocess.Popen(
        [sys.executable, str(ROOT / "demo" / "cloud_brain.py"), "--port", str(args.remote_port)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    wait_port(args.remote_port, True)
    mark("boot", text=f"remote brain up on :{args.remote_port}",
         detail="this stands in for a paid cloud endpoint; it is a separate process on a real socket")

    chain = Chain([
        http_json_provider("remote", f"http://127.0.0.1:{args.remote_port}/v1/chat/completions",
                           timeout_s=30),
        http_json_provider("local", f"http://127.0.0.1:{args.local_port}/v1/chat/completions",
                           model=args.local_model, api_key=args.local_key or local_key(),
                           timeout_s=180,
                           extras={"max_tokens": 48,
                                   # without this a reasoning model spends the whole
                                   # budget thinking and returns an empty answer
                                   "chat_template_kwargs": {"enable_thinking": False}}),
    ])

    runtime = Runtime(chain=chain, queue=SpeechQueue(), gate=ProactiveGate(),
                      state_provider=lambda: {"brain": {"providers": [
                          {"id": p.name, "reachable": p.enabled} for p in chain.providers]}})
    server = runtime.make_server(port=args.port)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    wait_port(args.port, True)
    mark("ready", text=f"runtime listening on :{args.port}", detail="chain: remote → local")
    adi = ADI(f"http://127.0.0.1:{args.port}")

    # Ask through the raw endpoint too, so we capture which provider answered.
    def ask_with_meta(question: str) -> None:
        import urllib.request
        started = time.time()
        req = urllib.request.Request(f"http://127.0.0.1:{args.port}/api/chat",
                                     data=json.dumps({"message": question}).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        meta, pieces, final, err = {}, [], None, None
        try:
            with opener.open(req, timeout=200) as resp:
                kind = ""
                for raw in resp:
                    line = raw.decode("utf-8", "replace").strip()
                    if line.startswith("event:"):
                        kind = line[6:].strip()
                    elif line.startswith("data:"):
                        data = json.loads(line[5:].strip())
                        if kind == "meta":
                            meta = data
                        elif kind == "delta":
                            pieces.append(data.get("text", ""))
                        elif kind == "final":
                            final = data
                        elif kind == "error":
                            err = data
        except Exception as exc:  # noqa: BLE001
            err = {"error": f"{type(exc).__name__}: {exc}"[:200]}
        mark("ask", question=question,
             provider=(final or meta).get("provider"),
             degraded=(final or meta).get("degraded"),
             attempts=meta.get("attempts") or (final or {}).get("attempts") or [],
             reply=(final or {}).get("reply") or "".join(pieces),
             error=(err or {}).get("error"),
             secs=round(time.time() - started, 2))

    # 1) online
    ask_with_meta(QUESTIONS[0])

    # 2) cut — a real process kill, not a flag
    cloud.terminate()
    cloud.wait(timeout=10)
    wait_port(args.remote_port, False)
    mark("cut", text=f"remote brain killed (pid {cloud.pid})",
         detail="a real ECONNREFUSED on the next call — the same path as a dead cloud API")

    # 3) offline
    ask_with_meta(QUESTIONS[1])

    # 4) restore
    cloud = subprocess.Popen(
        [sys.executable, str(ROOT / "demo" / "cloud_brain.py"), "--port", str(args.remote_port)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    wait_port(args.remote_port, True)
    mark("restore", text="remote brain back up", detail="no restart of the runtime, no lost state")

    # 5) online again
    ask_with_meta(QUESTIONS[2])

    cloud.terminate()
    server.shutdown()

    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                               "local_model": args.local_model,
                               "total_secs": round(time.time() - t0, 1),
                               "steps": steps}, ensure_ascii=False, indent=2))

    for step in steps:
        if step["kind"] == "ask":
            print(f"[{step['t']:>5.2f}s] provider={step.get('provider')} "
                  f"degraded={step.get('degraded')} {step.get('secs')}s")
            print(f"          Q: {step['question']}")
            print(f"          A: {(step.get('reply') or step.get('error') or '')[:180]}")
        else:
            print(f"[{step['t']:>5.2f}s] {step['text']}")
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
