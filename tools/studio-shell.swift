import AppKit
import WebKit

let serverURL = URL(string: "http://127.0.0.1:8720/")!

final class AppDelegate: NSObject, NSApplicationDelegate, WKScriptMessageHandler {
    var window: NSWindow!
    var webview: WKWebView!
    var server: Process?

    func applicationDidFinishLaunching(_ note: Notification) {
        // A server from an earlier launch may still own port 8720 (the old
        // process sometimes outlives its window). A second server could
        // never bind, so the app showed "server did not start" forever.
        // If the port already answers, just use it instead of a child.
        if !serverResponds() {
            startServer()
        }
        let cfg = WKWebViewConfiguration()
        // Enable developer tools for debugging
        cfg.preferences.setValue(true, forKey: "developerExtrasEnabled")
        // Add message handler for console logs
        let userContentController = WKUserContentController()
        userContentController.add(self, name: "consoleLog")
        userContentController.add(self, name: "xdj")
        cfg.userContentController = userContentController
        webview = WKWebView(frame: .zero, configuration: cfg)
        webview.navigationDelegate = self
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1320, height: 860),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable],
                          backing: .buffered, defer: false)
        window.title = "XDJ Mod Studio"
        window.contentView = webview
        window.center()
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        waitForServer(tries: 100)
    }

    func startServer() {
        // The frozen Python server bundled next to this app (built by
        // tools/build_studio.sh under Resources/server/); runs headless,
        // this window is its UI.
        let exe = Bundle.main.bundleURL
            .appendingPathComponent("Contents/Resources/server/XDJ Mod Studio")
        guard FileManager.default.fileExists(atPath: exe.path) else { 
            print("Server executable not found at \(exe.path)")
            return 
        }
        let p = Process()
        p.executableURL = exe
        // --parent: the server polls our pid and exits on its own if we die
        // without a clean quit (crash, force-quit, logout) -- no ghost
        // servers with a running emulator after the app is gone.
        p.arguments = ["--no-browser", "--parent",
                       String(ProcessInfo.processInfo.processIdentifier)]
        // Capture output for debugging
        let outPipe = Pipe()
        let errPipe = Pipe()
        p.standardOutput = outPipe
        p.standardError = errPipe
        outPipe.fileHandleForReading.readabilityHandler = { handle in
            let data = handle.availableData
            if let str = String(data: data, encoding: .utf8), !str.isEmpty {
                print("[SERVER OUT] \(str)")
            }
        }
        errPipe.fileHandleForReading.readabilityHandler = { handle in
            let data = handle.availableData
            if let str = String(data: data, encoding: .utf8), !str.isEmpty {
                print("[SERVER ERR] \(str)")
            }
        }
        do { try p.run(); server = p } catch { print("server failed: \(error)") }
    }

    func serverResponds() -> Bool {
        guard let data = try? Data(contentsOf: serverURL) else { return false }
        if (try? JSONSerialization.jsonObject(with: data) as? [String: Any]) != nil {
            return true
        }
        return String(data: data, encoding: .utf8)?.contains("XDJ Mod Studio") == true
    }

    func waitForServer(tries: Int) {
        var left = tries
        func tick() {
            var ok = false
            if let data = try? Data(contentsOf: serverURL),
               (try? JSONSerialization.jsonObject(with: data) as? [String: Any]) != nil
                || String(data: data, encoding: .utf8)?.contains("XDJ Mod Studio") == true {
                ok = true
            }
            print("[WAIT] Server check: ok=\(ok), left=\(left)")
            if ok {
                print("[WAIT] Loading webview...")
                webview.load(URLRequest(url: serverURL))
            } else if left > 0 {
                left -= 1
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.15, execute: tick)
            } else {
                print("[WAIT] Server did not start in time")
                let alert = NSAlert()
                alert.messageText = "Mod Studio server did not start"
                alert.runModal()
            }
        }
        tick()
    }

    func applicationWillTerminate(_ note: Notification) {
        guard let p = server else { return }
        p.terminate()                    // graceful (the server watches us too)
        // Old bundles' servers swallow SIGTERM (SDL's handlers), so wait a
        // moment and make sure the child is really gone before we exit.
        var waits = 15
        while p.isRunning && waits > 0 {
            Thread.sleep(forTimeInterval: 0.1)
            waits -= 1
        }
        if p.isRunning {
            kill(p.processIdentifier, SIGKILL)
        }
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        return true
    }
}

extension AppDelegate: WKNavigationDelegate {
    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        print("[WEBVIEW] Page loaded: \(webView.url?.absoluteString ?? "unknown")")
        // Test JavaScript execution
        webView.evaluateJavaScript("document.title") { result, error in
            if let error = error {
                print("[WEBVIEW] JS error: \(error)")
            } else {
                print("[WEBVIEW] JS result: \(result ?? "nil")")
            }
        }
        // Inject console logging
        webView.evaluateJavaScript("""
            console.log = (function(orig) { return function() { 
                var args = Array.from(arguments).map(String);
                window.webkit.messageHandlers.consoleLog.postMessage(args.join(' '));
            }; })(console.log);
            console.error = (function(orig) { return function() { 
                var args = Array.from(arguments).map(String);
                window.webkit.messageHandlers.consoleLog.postMessage('[ERROR] ' + args.join(' '));
            }; })(console.error);
        """) { _, _ in }
    }
    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        print("[WEBVIEW] Navigation failed: \(error)")
    }
    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        print("[WEBVIEW] Provisional navigation failed: \(error)")
    }
    
    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
        if message.name == "consoleLog" {
            print("[JS CONSOLE] \(message.body)")
            return
        }
        if message.name == "xdj",
           let dict = message.body as? [String: Any],
           let type_ = dict["type"] as? String, type_ == "pickFolder" || type_ == "pickFile" {
            let panel = NSOpenPanel()
            panel.canChooseFiles = type_ == "pickFile"
            panel.canChooseDirectories = type_ == "pickFolder"
            panel.allowsMultipleSelection = false
            if let prompt = dict["prompt"] as? String {
                panel.message = prompt
            }
            let wid = dict["id"] as? String ?? ""
            panel.begin { resp in
                var js = "__xdjPicked('\(wid)', null)"
                if resp == .OK, let url = panel.url {
                    let esc = url.path.replacingOccurrences(of: "'", with: "\\'")
                    js = "__xdjPicked('\(wid)', '\(esc)')"
                }
                self.webview?.evaluateJavaScript(js, completionHandler: nil)
            }
        }
    }
}

let app = NSApplication.shared
app.setActivationPolicy(.regular)
let del = AppDelegate()
app.delegate = del
let menu = NSMenu()
let item = NSMenuItem(title: "XDJ Mod Studio", action: nil, keyEquivalent: "")
let sub = NSMenu()
sub.addItem(NSMenuItem(title: "Quit XDJ Mod Studio", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q"))
item.submenu = sub
menu.addItem(item)
app.mainMenu = menu
app.run()
