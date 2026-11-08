"""python -m adi_runtime [--port 8899] [--provider echo|command:...] [--state file.json] [--data DIR]

Runs a standalone runtime with one provider. Useful as a smoke target, and as a
minimal example of wiring the three pieces together.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import Chain, ProactiveGate, Runtime, SpeechQueue, echo_provider


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="adi-runtime")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8899)
    ap.add_argument("--provider", default="echo",
                    help="echo | command:<argv with {prompt}> | http:<url>")
    ap.add_argument("--model", default="", help="model name for http providers")
    ap.add_argument("--api-key", default="", help="bearer token for http providers (or $ADI_LOCAL_KEY)")
    ap.add_argument("--preamble", default=(
        "You are ADI, running on the user's own machine. Answers come from a model "
        "server on this device; no network is required for you to answer. "
        "Do not claim to be a cloud service."))
    ap.add_argument("--state", default="", help="JSON file whose contents /state returns")
    ap.add_argument("--data", default=str(Path.home() / ".adi"),
                    help="directory for the speech trail and proactive cooldown state")
    args = ap.parse_args(argv)

    if args.provider.startswith("command:"):
        from . import command_provider
        chain = Chain([command_provider("command", args.provider.split(":", 1)[1].split())])
    elif args.provider.startswith("http:"):
        from . import http_json_provider
        import os
        key = args.api_key or os.environ.get("ADI_LOCAL_KEY") or os.environ.get("OPENAI_API_KEY") or None
        chain = Chain([http_json_provider(
            "http", args.provider.split(":", 1)[1], model=args.model or None, api_key=key,
            extras={"max_tokens": 512,
                    # reasoning models otherwise spend the whole budget thinking
                    "chat_template_kwargs": {"enable_thinking": False}})])
    else:
        chain = Chain([echo_provider("[echo] ")])

    def state() -> dict:
        if not args.state:
            return {}
        try:
            return json.loads(Path(args.state).read_text())
        except (OSError, ValueError):
            return {}

    data = Path(args.data)

    Runtime(
        chain=chain,
        # The trail is the audit log of "who made it say that", so it persists.
        queue=SpeechQueue(trail=data / "say.jsonl"),
        # Without a state file the cooldown never advances and every poll speaks.
        gate=ProactiveGate(state_file=data / "proactive.json"),
        state_provider=state,
        preamble=args.preamble,
    ).serve(args.host, args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
