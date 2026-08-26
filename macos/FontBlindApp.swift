import AppKit
import Darwin
import Foundation
import UniformTypeIdentifiers
import WebKit

private enum WrapperError: LocalizedError {
    case missingServer
    case launchFailed
    case invalidReadyMessage
    case startupTimedOut
    case serverExited

    var errorDescription: String? {
        switch self {
        case .missingServer:
            return "The bundled FontBlind engine is missing. Rebuild the app."
        case .launchFailed:
            return "The bundled FontBlind engine could not start."
        case .invalidReadyMessage:
            return "The bundled FontBlind engine returned an invalid local address."
        case .startupTimedOut:
            return "The bundled FontBlind engine took too long to start."
        case .serverExited:
            return "The bundled FontBlind engine stopped unexpectedly."
        }
    }
}

private final class ServerController {
    private let process = Process()
    private let outputPipe = Pipe()
    private let stateLock = NSLock()
    private var startCompletion: ((Result<URL, Error>) -> Void)?
    private var ready = false
    private var stopping = false

    var onUnexpectedExit: (() -> Void)?

    func start(completion: @escaping (Result<URL, Error>) -> Void) {
        guard let resources = Bundle.main.resourceURL else {
            completion(.failure(WrapperError.missingServer))
            return
        }
        let executable = resources
            .appendingPathComponent("server", isDirectory: true)
            .appendingPathComponent("FontBlindServer", isDirectory: false)
        guard FileManager.default.isExecutableFile(atPath: executable.path) else {
            completion(.failure(WrapperError.missingServer))
            return
        }

        stateLock.lock()
        startCompletion = completion
        stateLock.unlock()

        process.executableURL = executable
        process.currentDirectoryURL = executable.deletingLastPathComponent()
        process.standardInput = FileHandle.nullDevice
        process.standardOutput = outputPipe
        process.standardError = FileHandle.nullDevice

        var environment = ProcessInfo.processInfo.environment
        for key in ["PYTHONHOME", "PYTHONPATH", "PYTHONSTARTUP", "PYTHONINSPECT"] {
            environment.removeValue(forKey: key)
        }
        environment["PYTHONUNBUFFERED"] = "1"
        process.environment = environment

        process.terminationHandler = { [weak self] _ in
            guard let self else { return }
            let shouldNotify: Bool
            self.stateLock.lock()
            shouldNotify = self.ready && !self.stopping
            self.stateLock.unlock()
            self.finishStart(.failure(WrapperError.serverExited))
            if shouldNotify {
                DispatchQueue.main.async { self.onUnexpectedExit?() }
            }
        }

        do {
            try process.run()
        } catch {
            finishStart(.failure(WrapperError.launchFailed))
            return
        }

        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            self?.readReadyMessage()
        }
        DispatchQueue.global(qos: .utility).asyncAfter(deadline: .now() + 20) { [weak self] in
            guard let self else { return }
            self.stateLock.lock()
            let stillStarting = !self.ready && self.startCompletion != nil
            self.stateLock.unlock()
            if stillStarting {
                self.finishStart(.failure(WrapperError.startupTimedOut))
                self.stop()
            }
        }
    }

    private func readReadyMessage() {
        var data = Data()
        let handle = outputPipe.fileHandleForReading
        do {
            while data.count < 4_096 {
                guard let byte = try handle.read(upToCount: 1), !byte.isEmpty else {
                    finishStart(.failure(WrapperError.serverExited))
                    return
                }
                if byte[byte.startIndex] == 0x0A {
                    break
                }
                data.append(byte)
            }
        } catch {
            finishStart(.failure(WrapperError.serverExited))
            return
        }

        guard
            let line = String(data: data, encoding: .utf8),
            let url = Self.parseReadyLine(line)
        else {
            finishStart(.failure(WrapperError.invalidReadyMessage))
            stop()
            return
        }
        finishStart(.success(url))
    }

    private static func parseReadyLine(_ line: String) -> URL? {
        let parts = line.split(whereSeparator: { $0 == " " || $0 == "\t" })
        guard
            parts.count == 3,
            parts[0] == "FONTBLIND_READY",
            parts[1] == "127.0.0.1",
            let port = Int(parts[2]),
            (1...65_535).contains(port)
        else {
            return nil
        }
        var components = URLComponents()
        components.scheme = "http"
        components.host = "127.0.0.1"
        components.port = port
        components.path = "/"
        return components.url
    }

    private func finishStart(_ result: Result<URL, Error>) {
        let completion: ((Result<URL, Error>) -> Void)?
        stateLock.lock()
        completion = startCompletion
        startCompletion = nil
        if case .success = result {
            ready = true
        }
        stateLock.unlock()
        guard let completion else { return }
        DispatchQueue.main.async { completion(result) }
    }

    func stop() {
        stateLock.lock()
        stopping = true
        stateLock.unlock()
        guard process.isRunning else { return }

        process.terminate()
        let deadline = Date().addingTimeInterval(3)
        while process.isRunning && Date() < deadline {
            usleep(20_000)
        }
        if process.isRunning {
            kill(process.processIdentifier, SIGKILL)
            process.waitUntilExit()
        }
    }
}

private final class BrowserController: NSViewController, WKNavigationDelegate, WKUIDelegate, WKDownloadDelegate {
    private let baseURL: URL
    private var webView: WKWebView!

    init(baseURL: URL) {
        self.baseURL = baseURL
        super.init(nibName: nil, bundle: nil)
    }

    required init?(coder: NSCoder) {
        nil
    }

    override func loadView() {
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = WKWebsiteDataStore.nonPersistent()
        configuration.defaultWebpagePreferences.allowsContentJavaScript = true
        configuration.preferences.javaScriptCanOpenWindowsAutomatically = false

        webView = WKWebView(frame: .zero, configuration: configuration)
        webView.navigationDelegate = self
        webView.uiDelegate = self
        view = webView

        let request = URLRequest(
            url: baseURL,
            cachePolicy: .reloadIgnoringLocalAndRemoteCacheData,
            timeoutInterval: 20
        )
        webView.load(request)
    }

    private func isAllowed(_ url: URL?) -> Bool {
        guard let url else { return false }
        if url.absoluteString == "about:blank" {
            return true
        }
        return url.scheme?.lowercased() == "http"
            && url.host?.lowercased() == "127.0.0.1"
            && url.port == baseURL.port
            && url.user == nil
            && url.password == nil
    }

    private func isDownload(_ url: URL?) -> Bool {
        guard isAllowed(url), let path = url?.path else { return false }
        return path.hasPrefix("/download/")
    }

    func webView(
        _ webView: WKWebView,
        decidePolicyFor navigationAction: WKNavigationAction,
        preferences: WKWebpagePreferences,
        decisionHandler: @escaping (WKNavigationActionPolicy, WKWebpagePreferences) -> Void
    ) {
        guard isAllowed(navigationAction.request.url) else {
            decisionHandler(.cancel, preferences)
            return
        }
        if navigationAction.shouldPerformDownload || isDownload(navigationAction.request.url) {
            decisionHandler(.download, preferences)
        } else {
            decisionHandler(.allow, preferences)
        }
    }

    func webView(
        _ webView: WKWebView,
        decidePolicyFor navigationResponse: WKNavigationResponse,
        decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void
    ) {
        guard isAllowed(navigationResponse.response.url) else {
            decisionHandler(.cancel)
            return
        }
        if !navigationResponse.canShowMIMEType && navigationResponse.isForMainFrame {
            decisionHandler(.download)
        } else {
            decisionHandler(.allow)
        }
    }

    func webView(_ webView: WKWebView, navigationAction: WKNavigationAction, didBecome download: WKDownload) {
        download.delegate = self
    }

    func webView(_ webView: WKWebView, navigationResponse: WKNavigationResponse, didBecome download: WKDownload) {
        download.delegate = self
    }

    func webView(
        _ webView: WKWebView,
        runOpenPanelWith parameters: WKOpenPanelParameters,
        initiatedByFrame frame: WKFrameInfo,
        completionHandler: @escaping ([URL]?) -> Void
    ) {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = false
        panel.canChooseFiles = true
        panel.allowsMultipleSelection = parameters.allowsMultipleSelection
        let fontTypes = ["ttf", "otf"].compactMap { UTType(filenameExtension: $0) }
        if !fontTypes.isEmpty {
            panel.allowedContentTypes = fontTypes
        }
        guard let window = webView.window else {
            completionHandler(nil)
            return
        }
        panel.beginSheetModal(for: window) { response in
            completionHandler(response == .OK ? panel.urls : nil)
        }
    }

    func download(
        _ download: WKDownload,
        decideDestinationUsing response: URLResponse,
        suggestedFilename: String,
        completionHandler: @escaping (URL?) -> Void
    ) {
        guard isDownload(response.url), let window = webView.window else {
            completionHandler(nil)
            return
        }

        let panel = NSSavePanel()
        let safeName = (suggestedFilename as NSString).lastPathComponent
        panel.nameFieldStringValue = safeName.isEmpty ? "fontblind-download" : safeName
        panel.canCreateDirectories = true
        panel.isExtensionHidden = false
        if let type = UTType(filenameExtension: (panel.nameFieldStringValue as NSString).pathExtension) {
            panel.allowedContentTypes = [type]
        }
        panel.beginSheetModal(for: window) { response in
            completionHandler(response == .OK ? panel.url : nil)
        }
    }

    func download(_ download: WKDownload, didFailWithError error: Error, resumeData: Data?) {
        presentError("The download could not be saved.")
    }

    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
        webView.load(URLRequest(url: baseURL, cachePolicy: .reloadIgnoringLocalAndRemoteCacheData))
    }

    private func presentError(_ message: String) {
        DispatchQueue.main.async { [weak self] in
            guard let window = self?.webView.window else { return }
            let alert = NSAlert()
            alert.alertStyle = .warning
            alert.messageText = "FontBlind"
            alert.informativeText = message
            alert.beginSheetModal(for: window)
        }
    }
}

private final class StatusController: NSViewController {
    init(message: String, showsQuit: Bool = false) {
        super.init(nibName: nil, bundle: nil)

        let container = NSView()
        let label = NSTextField(wrappingLabelWithString: message)
        label.alignment = .center
        label.font = .systemFont(ofSize: 15, weight: .medium)
        label.textColor = .secondaryLabelColor
        label.translatesAutoresizingMaskIntoConstraints = false
        container.addSubview(label)

        var constraints = [
            label.centerXAnchor.constraint(equalTo: container.centerXAnchor),
            label.centerYAnchor.constraint(equalTo: container.centerYAnchor),
            label.widthAnchor.constraint(lessThanOrEqualToConstant: 480),
        ]
        if showsQuit {
            let button = NSButton(title: "Quit FontBlind", target: NSApp, action: #selector(NSApplication.terminate(_:)))
            button.bezelStyle = .rounded
            button.translatesAutoresizingMaskIntoConstraints = false
            container.addSubview(button)
            constraints += [
                button.topAnchor.constraint(equalTo: label.bottomAnchor, constant: 20),
                button.centerXAnchor.constraint(equalTo: container.centerXAnchor),
            ]
        }
        NSLayoutConstraint.activate(constraints)
        view = container
    }

    required init?(coder: NSCoder) {
        nil
    }
}

private final class AppDelegate: NSObject, NSApplicationDelegate {
    private let server = ServerController()
    private var window: NSWindow!
    private var browser: BrowserController?

    func applicationDidFinishLaunching(_ notification: Notification) {
        configureMenus()
        createWindow()
        server.onUnexpectedExit = { [weak self] in
            self?.showFailure(WrapperError.serverExited.localizedDescription)
        }
        server.start { [weak self] result in
            switch result {
            case .success(let url):
                guard let self else { return }
                let browser = BrowserController(baseURL: url)
                self.browser = browser
                self.window.contentViewController = browser
            case .failure(let error):
                self?.showFailure(error.localizedDescription)
            }
        }
        NSApp.activate(ignoringOtherApps: true)
    }

    func applicationWillTerminate(_ notification: Notification) {
        server.stop()
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        true
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        if !flag {
            window.makeKeyAndOrderFront(nil)
        }
        return true
    }

    func applicationSupportsSecureRestorableState(_ app: NSApplication) -> Bool {
        true
    }

    private func createWindow() {
        window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 1_120, height: 760),
            styleMask: [.titled, .closable, .miniaturizable, .resizable],
            backing: .buffered,
            defer: false
        )
        window.title = "FontBlind"
        window.minSize = NSSize(width: 760, height: 600)
        window.tabbingMode = .disallowed
        window.isReleasedWhenClosed = false
        window.isRestorable = false
        window.collectionBehavior.insert(.fullScreenPrimary)
        window.contentViewController = StatusController(message: "Starting FontBlind locally…")
        window.center()
        window.makeKeyAndOrderFront(nil)
    }

    private func showFailure(_ message: String) {
        browser = nil
        window.contentViewController = StatusController(message: message, showsQuit: true)
        window.makeKeyAndOrderFront(nil)
    }

    private func configureMenus() {
        let mainMenu = NSMenu()
        let appMenuItem = NSMenuItem()
        mainMenu.addItem(appMenuItem)
        let appMenu = NSMenu()
        appMenu.addItem(
            withTitle: "About FontBlind",
            action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)),
            keyEquivalent: ""
        )
        appMenu.addItem(.separator())
        appMenu.addItem(
            withTitle: "Quit FontBlind",
            action: #selector(NSApplication.terminate(_:)),
            keyEquivalent: "q"
        )
        appMenuItem.submenu = appMenu

        let editMenuItem = NSMenuItem()
        mainMenu.addItem(editMenuItem)
        let editMenu = NSMenu(title: "Edit")
        editMenu.addItem(withTitle: "Undo", action: Selector(("undo:")), keyEquivalent: "z")
        editMenu.addItem(withTitle: "Redo", action: Selector(("redo:")), keyEquivalent: "Z")
        editMenu.addItem(.separator())
        editMenu.addItem(withTitle: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        editMenu.addItem(withTitle: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        editMenu.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        editMenu.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        editMenuItem.submenu = editMenu

        NSApp.mainMenu = mainMenu
    }
}

@main
private struct FontBlindApplication {
    static func main() {
        let application = NSApplication.shared
        let delegate = AppDelegate()
        application.delegate = delegate
        application.setActivationPolicy(.regular)
        application.run()
    }
}
