# adi-app

macOS desktop shell: supervises the runtime, renders the cockpit, speaks the replies.

**Status:** the private build is a working `.app` (AppKit + WKWebView, ad-hoc signed).
The public cut still has to lose every reference to the private system before it ships.

What it does that a browser tab cannot:

- starts and supervises the local runtime, restarts it if it dies
- single-instance, remembers window frame
- native menu, real status bar fed by `/api/state`
- pulls `/api/say/poll` every 2 s and speaks replies with a local neural voice
- falls back to the system voice when the neural engine is unavailable
