import SwiftUI
import WebKit

/// Runs a game's HTML the way the website does: inside an iframe with
/// `sandbox="allow-scripts"`, so the game has its own opaque origin and no network.
/// A small host page (in the WKWebView) relays messages:
///   app -> host page -> game:  `state_changed` (via `window.receiveNative`)
///   game -> host page -> app:  `make_move` (via the "game" WKScriptMessageHandler)
/// Same approach as the online bridge in frontends/ios-game-portal.
struct GameWebView: UIViewRepresentable {
    let code: String
    /// The `state_changed` JSON to show, or nil to send nothing (e.g. during a handoff).
    let stateJSON: String?
    /// Bump to send the same message again (the README asks for this after a rejected move).
    var resendCount = 0
    let onMessage: (String) -> Void

    func makeCoordinator() -> Coordinator { Coordinator(self) }

    func makeUIView(context: Context) -> WKWebView {
        let config = WKWebViewConfiguration()
        config.websiteDataStore = .nonPersistent()
        config.userContentController.add(context.coordinator, name: "game")
        let view = WKWebView(frame: .zero, configuration: config)
        view.navigationDelegate = context.coordinator
        view.isOpaque = false
        view.backgroundColor = .clear
        view.scrollView.backgroundColor = .clear
        view.scrollView.isScrollEnabled = false // the iframe scrolls itself
        #if DEBUG
        view.isInspectable = true // Safari > Develop can inspect the game
        #endif
        view.loadHTMLString(Self.hostPage(code: code), baseURL: nil)
        return view
    }

    func updateUIView(_ view: WKWebView, context: Context) {
        context.coordinator.parent = self
        context.coordinator.send(view)
    }

    static func dismantleUIView(_ view: WKWebView, coordinator: Coordinator) {
        view.configuration.userContentController.removeScriptMessageHandler(forName: "game")
        view.navigationDelegate = nil
    }

    /// The host page. Its CSP blocks all network; the game's code is passed as base64 so
    /// nothing in it can break out of this page's script.
    private static func hostPage(code: String) -> String {
        let encoded = Data(code.utf8).base64EncodedString()
        return """
        <!doctype html><html><head><meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; frame-src about:">
        <style>html,body{margin:0;height:100%;background:transparent}iframe{border:0;width:100%;height:100%;display:block}</style>
        </head><body><iframe id="game" title="Game" sandbox="allow-scripts"></iframe><script>
        const frame = document.getElementById("game");
        let latest = null, loaded = false;
        window.receiveNative = (json) => {
          latest = JSON.parse(json);
          if (loaded) frame.contentWindow.postMessage(latest, "*");
        };
        frame.onload = () => { loaded = true; if (latest) frame.contentWindow.postMessage(latest, "*"); };
        window.addEventListener("message", (event) => {
          if (event.source !== frame.contentWindow) return;
          window.webkit.messageHandlers.game.postMessage(JSON.stringify(event.data ?? null));
        });
        frame.srcdoc = new TextDecoder().decode(Uint8Array.from(atob("\(encoded)"), (c) => c.charCodeAt(0)));
        </script></body></html>
        """
    }

    final class Coordinator: NSObject, WKNavigationDelegate, WKScriptMessageHandler {
        var parent: GameWebView
        private var ready = false
        private var lastSent: String?

        init(_ parent: GameWebView) { self.parent = parent }

        func webView(_ view: WKWebView, didFinish navigation: WKNavigation!) {
            ready = true
            send(view)
        }

        /// Sends the current `state_changed` if it changed since the last one.
        func send(_ view: WKWebView) {
            guard ready, let json = parent.stateJSON else { return }
            let key = "\(parent.resendCount)|\(json)"
            guard key != lastSent else { return }
            lastSent = key
            view.callAsyncJavaScript("window.receiveNative(json)", arguments: ["json": json], in: nil, in: .page) { _ in }
        }

        func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) {
            guard message.frameInfo.isMainFrame, let json = message.body as? String else { return }
            parent.onMessage(json)
        }

        /// Only the host page itself (about:blank) and the game's srcdoc iframe may load.
        func webView(_ view: WKWebView, decidePolicyFor action: WKNavigationAction,
                     decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
            decisionHandler(action.request.url?.scheme == "about" ? .allow : .cancel)
        }
    }
}
