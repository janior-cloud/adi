// ADI — the macOS shell.
//
// It does four things and nothing else: make sure exactly one copy is running,
// start the runtime if it is not already listening, show its page in a native
// window, and speak whatever the runtime puts in the speech queue.
//
// The shell deliberately owns no agent logic. Everything it knows about the
// system is the six HTTP endpoints, which is what keeps the runtime replaceable.

import Cocoa
import WebKit

// MARK: - Configuration

struct Config {
    var port: Int = 8899
    var python: String = "/usr/bin/env"
    var pythonArgs: [String] = ["python3.11"]
    var runtimeDir: String = NSString(string: "~/ADI/adi-runtime").expandingTildeInPath
    var provider: String = "echo"
    var model: String = ""
    var apiKeyEnv: String = "ADI_LOCAL_KEY"
    var speak: Bool = true
    var voice: String = ""
    var env: [String: String] = [:]          // extra environment for the runtime
    var envFile: String = ""                 // KEY=VALUE file, chmod 600, never in git

    /// Read a KEY=VALUE file. Keys are not stored in config.json so that a config
    /// can be shared or shown in a bug report without leaking a credential.
    static func parseEnvFile(_ path: String) -> [String: String] {
        let full = NSString(string: path).expandingTildeInPath
        guard let text = try? String(contentsOfFile: full, encoding: .utf8) else { return [:] }
        var out: [String: String] = [:]
        for line in text.split(separator: "\n") {
            let trimmed = line.trimmingCharacters(in: .whitespaces)
            if trimmed.isEmpty || trimmed.hasPrefix("#") { continue }
            guard let eq = trimmed.firstIndex(of: "=") else { continue }
            let key = String(trimmed[trimmed.startIndex..<eq]).trimmingCharacters(in: .whitespaces)
            var value = String(trimmed[trimmed.index(after: eq)...]).trimmingCharacters(in: .whitespaces)
            if value.count >= 2, (value.hasPrefix("\"") && value.hasSuffix("\"")) {
                value = String(value.dropFirst().dropLast())
            }
            out[key] = value
        }
        return out
    }

    static func load() -> Config {
        var cfg = Config()
        let support = FileManager.default
            .urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("ADI", isDirectory: true)
        let file = support.appendingPathComponent("config.json")
        guard let data = try? Data(contentsOf: file),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            return cfg
        }
        if let v = obj["port"] as? Int { cfg.port = v }
        if let v = obj["python"] as? String { cfg.python = v }
        if let v = obj["pythonArgs"] as? [String] { cfg.pythonArgs = v }
        if let v = obj["runtimeDir"] as? String { cfg.runtimeDir = NSString(string: v).expandingTildeInPath }
        if let v = obj["provider"] as? String { cfg.provider = v }
        if let v = obj["model"] as? String { cfg.model = v }
        if let v = obj["apiKeyEnv"] as? String { cfg.apiKeyEnv = v }
        if let v = obj["speak"] as? Bool { cfg.speak = v }
        if let v = obj["voice"] as? String { cfg.voice = v }
        if let v = obj["env"] as? [String: String] { cfg.env = v }
        if let v = obj["envFile"] as? String { cfg.envFile = v }
        return cfg
    }
}

// MARK: - Logging

/// A shell that fails silently is worse than one that refuses to start, and a
/// GUI launched by double-click has nowhere to print. Everything goes to
/// ~/Library/Logs/ADI.log.
enum Log {
    static let url = URL(fileURLWithPath: NSHomeDirectory())
        .appendingPathComponent("Library/Logs/ADI.log")

    static func w(_ message: String) {
        let line = "\(ISO8601DateFormatter().string(from: Date())) \(message)\n"
        let data = Data(line.utf8)
        if let handle = try? FileHandle(forWritingTo: url) {
            handle.seekToEndOfFile()
            handle.write(data)
            try? handle.close()
        } else {
            try? data.write(to: url)
        }
    }
}

/// One instance per user, via an exclusive lock. Checking for another process
/// with the same bundle id is unreliable when the same app exists in two places
/// (build/ and ~/Applications/), and that mistake produces an app that starts,
/// believes another copy is running, and quits before drawing a window.
enum SingleInstance {
    static var fd: Int32 = -1

    static func claim() -> Bool {
        let dir = URL(fileURLWithPath: NSHomeDirectory())
            .appendingPathComponent("Library/Application Support/ADI")
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let path = dir.appendingPathComponent("instance.lock").path
        fd = open(path, O_CREAT | O_RDWR, 0o600)
        if fd < 0 { return true }                      // cannot lock: do not block the user
        if flock(fd, LOCK_EX | LOCK_NB) != 0 { close(fd); fd = -1; return false }
        return true
    }
}

// MARK: - Helpers

func portOpen(_ port: Int) -> Bool {
    let s = socket(AF_INET, SOCK_STREAM, 0)
    guard s >= 0 else { return false }
    defer { close(s) }
    var addr = sockaddr_in()
    addr.sin_family = sa_family_t(AF_INET)
    addr.sin_port = in_port_t(UInt16(port).bigEndian)
    addr.sin_addr.s_addr = inet_addr("127.0.0.1")
    var tv = timeval(tv_sec: 0, tv_usec: 120_000)
    setsockopt(s, SOL_SOCKET, SO_SNDTIMEO, &tv, socklen_t(MemoryLayout<timeval>.size))
    let ok = withUnsafePointer(to: &addr) {
        $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
            connect(s, $0, socklen_t(MemoryLayout<sockaddr_in>.size)) == 0
        }
    }
    return ok
}

// MARK: - Runtime supervision

final class Runtime {
    let cfg: Config
    private var process: Process?
    private(set) var startedByUs = false

    init(_ cfg: Config) { self.cfg = cfg }

    var baseURL: URL { URL(string: "http://127.0.0.1:\(cfg.port)/")! }

    /// Returns true when something is already answering on the port.
    func isUp() -> Bool { portOpen(cfg.port) }

    func startIfNeeded() -> String? {
        if isUp() { return nil }                     // an existing instance wins
        let p = Process()
        p.executableURL = URL(fileURLWithPath: cfg.python)
        var args = cfg.pythonArgs + ["-m", "adi_runtime", "--port", "\(cfg.port)"]
        if !cfg.provider.isEmpty { args += ["--provider", cfg.provider] }
        if !cfg.model.isEmpty { args += ["--model", cfg.model] }
        p.arguments = args
        p.currentDirectoryURL = URL(fileURLWithPath: cfg.runtimeDir)
        var env = ProcessInfo.processInfo.environment
        env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:" + (env["PATH"] ?? "")
        for (k, v) in cfg.env { env[k] = v }
        if !cfg.envFile.isEmpty {
            let fromFile = Config.parseEnvFile(cfg.envFile)
            for (k, v) in fromFile { env[k] = v }
            Log.w("env file \(cfg.envFile): \(fromFile.count) key(s)")
        }
        // Re-name the credential instead of passing it on the command line:
        // `ps` is world-readable on macOS, argv is not a place for a secret.
        if !cfg.apiKeyEnv.isEmpty, let key = env[cfg.apiKeyEnv], !key.isEmpty {
            env["ADI_LOCAL_KEY"] = key
            Log.w("api key: taken from \(cfg.apiKeyEnv)")
        }
        p.environment = env
        let pipe = Pipe()
        p.standardOutput = pipe
        p.standardError = pipe
        do {
            try p.run()
        } catch {
            return "could not start the runtime: \(error.localizedDescription)"
        }
        process = p
        startedByUs = true

        // Wait for it to answer, but never longer than a few seconds silently.
        let deadline = Date().addingTimeInterval(8)
        while Date() < deadline {
            if isUp() { return nil }
            if !p.isRunning { return "the runtime exited immediately — check `\(cfg.pythonArgs.joined(separator: " ")) -m adi_runtime`" }
            usleep(120_000)
        }
        return "the runtime did not start listening on port \(cfg.port)"
    }

    func stop() {
        guard let p = process, startedByUs, p.isRunning else { return }
        p.terminate()
        let deadline = Date().addingTimeInterval(3)
        while p.isRunning && Date() < deadline { usleep(80_000) }
        if p.isRunning { kill(p.processIdentifier, SIGKILL) }
    }
}

// MARK: - Speech

final class Speaker {
    private var cursor = 0
    private var boot = ""
    private var timer: Timer?
    private var speaking = false
    private let cfg: Config
    private var baseURL: URL { URL(string: "http://127.0.0.1:\(cfg.port)/")! }

    init(_ cfg: Config) { self.cfg = cfg }

    func begin() {
        timer = Timer.scheduledTimer(withTimeInterval: 2.0, repeats: true) { _ in self.poll() }
    }

    func stop() { timer?.invalidate(); timer = nil }

    private func poll() {
        guard !speaking else { return }
        var comps = URLComponents(url: baseURL.appendingPathComponent("api/say/poll"),
                                  resolvingAgainstBaseURL: false)!
        comps.queryItems = [URLQueryItem(name: "after", value: "\(cursor)"),
                            URLQueryItem(name: "boot", value: boot)]
        var req = URLRequest(url: comps.url!)
        req.timeoutInterval = 2
        URLSession.shared.dataTask(with: req) { data, _, _ in
            guard let data,
                  let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return }
            let newBoot = obj["boot"] as? String ?? ""
            let reset = obj["reset"] as? Bool ?? false
            let items = obj["items"] as? [[String: Any]] ?? []
            DispatchQueue.main.async {
                self.boot = newBoot
                if reset { self.cursor = 0 }
                if let last = items.last, let id = last["id"] as? Int { self.cursor = id }
                guard let first = items.first, let text = first["text"] as? String else { return }
                self.say(text)
            }
        }.resume()
    }

    private func say(_ text: String) {
        speaking = true
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/say")
        p.arguments = cfg.voice.isEmpty ? [text] : ["-v", cfg.voice, text]
        p.terminationHandler = { _ in DispatchQueue.main.async { self.speaking = false } }
        try? p.run()
    }
}

// MARK: - App

final class AppDelegate: NSObject, NSApplicationDelegate, WKNavigationDelegate {
    private var window: NSWindow!
    private var webView: WKWebView!
    private var statusItem: NSStatusItem?
    private var speaker: Speaker!
    private var runtime: Runtime!
    private let cfg = Config.load()

    func applicationDidFinishLaunching(_ note: Notification) {
        Log.w("launch: port=\(cfg.port) provider=\(cfg.provider) dir=\(cfg.runtimeDir)")
        guard SingleInstance.claim() else {
            Log.w("launch: another instance holds the lock; activating it and leaving")
            for app in NSRunningApplication.runningApplications(withBundleIdentifier: "dev.adi.app")
            where app != NSRunningApplication.current {
                app.activate(options: [.activateAllWindows])
            }
            exit(0)
        }

        buildMenu()
        Log.w("menu built")
        buildWindow()
        Log.w("window built: \(window.frame)")
        buildStatusItem()

        runtime = Runtime(cfg)
        let problem = runtime.startIfNeeded()
        Log.w("runtime startIfNeeded -> \(problem ?? "already up")")

        speaker = Speaker(cfg)
        if cfg.speak { speaker.begin() }

        webView.load(URLRequest(url: runtime.baseURL))
        if let problem {
            // The page cannot render without the runtime, so say it in the window
            // instead of leaving the user with a blank grey rectangle.
            webView.loadHTMLString(errorPage(problem), baseURL: nil)
        }
        NSApp.activate(ignoringOtherApps: true)
        window.makeKeyAndOrderFront(nil)
        Log.w("launch complete; front window visible=\(window.isVisible)")

        // Driven by ADI_SELFTEST=1: exercises the real path a person takes
        // (type, submit, stream into the DOM) so the shell can be verified
        // without borrowing the mouse.
        if ProcessInfo.processInfo.environment["ADI_SELFTEST"] == "1" {
            DispatchQueue.main.asyncAfter(deadline: .now() + 3.0) { self.selfTest() }
        }
    }

    func applicationWillTerminate(_ note: Notification) {
        speaker?.stop()
        runtime?.stop()
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    private func buildWindow() {
        let rect = NSRect(x: 0, y: 0, width: 940, height: 700)
        window = NSWindow(contentRect: rect,
                          styleMask: [.titled, .closable, .miniaturizable, .resizable],
                          backing: .buffered, defer: false)
        window.title = "ADI"
        window.appearance = NSAppearance(named: .darkAqua)   // the page is dark; a light titlebar fights it
        window.titlebarAppearsTransparent = true
        window.backgroundColor = NSColor(calibratedRed: 0.027, green: 0.043, blue: 0.071, alpha: 1)
        window.center()
        window.setFrameAutosaveName("ADIMainWindow")
        window.minSize = NSSize(width: 520, height: 420)

        let conf = WKWebViewConfiguration()
        conf.preferences.setValue(true, forKey: "developerExtrasEnabled")
        webView = WKWebView(frame: rect, configuration: conf)
        webView.navigationDelegate = self
        webView.setValue(false, forKey: "drawsBackground")   // no white flash
        Log.w("webview created")
        window.contentView = webView
        window.makeKeyAndOrderFront(nil)
        if let saved = UserDefaults.standard.string(forKey: "ADIMainWindow") {
            _ = saved  // setFrameAutosaveName already restored the frame
        }
    }

    private func buildStatusItem() {
        let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        item.button?.title = "ADI"
        let menu = NSMenu()
        menu.addItem(NSMenuItem(title: "Open window", action: #selector(showWindow), keyEquivalent: "o"))
        menu.addItem(NSMenuItem(title: "Copy runtime URL", action: #selector(copyURL), keyEquivalent: "c"))
        menu.addItem(.separator())
        menu.addItem(NSMenuItem(title: "Quit ADI", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q"))
        item.menu = menu
        statusItem = item
    }

    private func buildMenu() {
        let main = NSMenu()

        let appItem = NSMenuItem()
        let appMenu = NSMenu()
        appMenu.addItem(NSMenuItem(title: "About ADI", action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)), keyEquivalent: ""))
        appMenu.addItem(.separator())
        appMenu.addItem(NSMenuItem(title: "Hide ADI", action: #selector(NSApplication.hide(_:)), keyEquivalent: "h"))
        appMenu.addItem(NSMenuItem(title: "Quit ADI", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q"))
        appItem.submenu = appMenu
        main.addItem(appItem)

        let viewItem = NSMenuItem()
        let viewMenu = NSMenu(title: "View")
        viewMenu.addItem(NSMenuItem(title: "Reload", action: #selector(reload), keyEquivalent: "r"))
        viewMenu.addItem(NSMenuItem(title: "Open in Browser", action: #selector(openInBrowser), keyEquivalent: "b"))
        viewMenu.addItem(NSMenuItem(title: "Copy Runtime URL", action: #selector(copyURL), keyEquivalent: ""))
        viewItem.submenu = viewMenu
        main.addItem(viewItem)

        NSApp.mainMenu = main
    }

    private func selfTest() {
        let js = """
        (() => {
          const q = document.getElementById('q');
          if (!q) return 'no-input';
          q.value = 'Explain in one sentence where you run.';
          document.getElementById('f').dispatchEvent(new Event('submit', {cancelable: true}));
          return 'submitted';
        })()
        """
        webView.evaluateJavaScript(js) { result, error in
            Log.w("selftest submit: \(result ?? "nil") err=\(error?.localizedDescription ?? "none")")
            DispatchQueue.main.asyncAfter(deadline: .now() + 6.0) {
                self.webView.evaluateJavaScript("document.getElementById('log').innerText") { text, _ in
                    Log.w("selftest transcript: \((text as? String ?? "").prefix(400))")
                }
            }
        }
    }

    @objc private func reload() { webView.load(URLRequest(url: runtime.baseURL)) }
    @objc private func showWindow() { NSApp.activate(ignoringOtherApps: true); window.makeKeyAndOrderFront(nil) }
    @objc private func copyURL() {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(runtime.baseURL.absoluteString, forType: .string)
    }
    @objc private func openInBrowser() { NSWorkspace.shared.open(runtime.baseURL) }

    private func errorPage(_ message: String) -> String {
        """
        <html><head><meta charset="utf-8"><style>
        body{background:#070b12;color:#e8f1ff;font:15px/1.7 -apple-system,sans-serif;
             display:flex;align-items:center;justify-content:center;height:100vh;margin:0}
        .box{max-width:520px;padding:28px;border:1px solid #24384f;border-radius:14px;background:#0d1622}
        h1{font-size:16px;letter-spacing:.1em;margin:0 0 12px}
        code{background:#101c2a;padding:2px 6px;border-radius:5px;font-size:13px}
        p{color:#8fa6c4;margin:10px 0}
        </style></head><body><div class="box">
        <h1>THE RUNTIME DID NOT START</h1>
        <p>\(message)</p>
        <p>Start it yourself with <code>python3 -m adi_runtime --port \(cfg.port)</code>
        in <code>\(cfg.runtimeDir)</code>, then press &#8984;R.</p>
        </div></body></html>
        """
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
