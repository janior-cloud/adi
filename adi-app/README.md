# adi-app — the macOS shell

A native window around the runtime. It is deliberately thin: it owns exactly
four responsibilities and no agent logic at all.

1. **One instance.** An exclusive `flock` on `~/Library/Application Support/ADI/instance.lock`.
   A second launch activates the first and exits.
2. **Supervision.** If nothing is listening on the port, it starts
   `python -m adi_runtime` in the configured runtime directory, waits at most 8s,
   and reports a real error in the window if it never comes up.
3. **The window.** A `WKWebView` pointed at `http://127.0.0.1:<port>/`. That is the
   whole UI layer — the page is served by the runtime itself (`adi_runtime/ui.py`),
   so the shell and any browser render the same thing.
4. **Speech.** Polls `/api/say/poll` every 2s and speaks new items with `/usr/bin/say`.
   Local TTS, no network, no permissions.

Everything else — providers, fallback, what was said — lives behind the six HTTP
endpoints, which is what keeps the shell replaceable and the runtime usable
without it.

**Status:** builds and runs; verified end to end on macOS 27 (Apple silicon).

## Build

```bash
./build.sh              # -> build/ADI.app
./build.sh --install    # -> ~/Applications/ADI.app
```

Ad-hoc signed. Fine on the machine that built it; shipping it to other people
needs a Developer ID and notarisation, which this does not pretend to do.

## Configure

`~/Library/Application Support/ADI/config.json` (all fields optional):

```json
{
  "port": 8899,
  "pythonArgs": ["python3.11"],
  "runtimeDir": "~/ADI/adi-runtime",
  "provider": "http:http://127.0.0.1:8080/v1/chat/completions",
  "model": "your-model-name",
  "envFile": "~/.adi/runtime.env",
  "apiKeyEnv": "ADI_LOCAL_KEY",
  "speak": true,
  "voice": "Tingting"
}
```

`envFile` is a `KEY=VALUE` file (`chmod 600`) merged into the runtime's
environment; `apiKeyEnv` names the variable inside it that holds the credential,
which is re-exported as `ADI_LOCAL_KEY` for the runtime — never on the command
line, because `ps` shows argv to every user on the machine. Credentials belong there, not in `config.json` — a config file gets
pasted into bug reports, and `env` objects get screenshotted.

```bash
printf 'ADI_LOCAL_KEY=%s\n' "$(pbpaste)" > ~/.adi/runtime.env && chmod 600 ~/.adi/runtime.env
```

## Verify it without a mouse

```bash
ADI_SELFTEST=1 ./build/ADI.app/Contents/MacOS/ADI
tail -f ~/Library/Logs/ADI.log
```

`ADI_SELFTEST=1` types a question into the page, submits it, and logs the
transcript once the answer has streamed in — the same path a person takes,
including the SSE reader in the DOM.

## Traps this code already paid for

- **`swiftc -O` never finished** on this file with Swift 6.4 (killed after 7
  minutes). `-Osize` compiles in 2.5s. The app is a WebView with three menus;
  there is nothing to optimise.
- **Never run the app binary from a build script as a "smoke test".** It launches
  the run loop and never returns, so the build hangs forever with no output.
- **Bundle-identifier single-instance checks are wrong** when the same app exists
  in two places (`build/` and `~/Applications/`): the second copy believes a peer
  is running and quits before drawing a window. Use a lock file.
- **A light titlebar over a dark page** looks broken; set `NSAppearance(.darkAqua)`
  and make the titlebar transparent.
- **A local model will claim to run in a data centre** unless the runtime tells it
  otherwise — see `--preamble` in `adi_runtime`. That is a product detail, not a
  cosmetic one.
- **A GUI that fails silently is worse than one that refuses to start.** Every
  launch writes `~/Library/Logs/ADI.log`; a double-clicked app has nowhere to print.
