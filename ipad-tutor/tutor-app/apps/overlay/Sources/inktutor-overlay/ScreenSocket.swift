import Foundation
import OverlayCore

/// The helper's connection to `ws://127.0.0.1:<port>/ws/screen/<id>`: URLSessionWebSocketTask, reconnect with
/// backoff, a ping every 15 s to notice a dead server. Every callback runs on the main queue.
final class ScreenSocket: NSObject, URLSessionWebSocketDelegate {
    let url: URL
    var onMessage: (ServerMessage) -> Void = { _ in }
    var onConnected: (Bool) -> Void = { _ in }
    var helloProvider: () -> ScreenHello = { ScreenHello(helper: helperName, displays: []) }
    var onSend: ((String) -> Void)?  // every message as sent (the --record recorder)

    private var session: URLSession!
    private var task: URLSessionWebSocketTask?
    private var generation = 0  // bumps per connection attempt; stale callbacks are ignored
    private var backoff = Backoff()
    private var open = false
    private var closing = false
    private var pingTimer: Timer?
    private var announcedOffline = false

    init(url: URL) {
        self.url = url
        super.init()
        let cfg = URLSessionConfiguration.ephemeral
        cfg.connectionProxyDictionary = [:]  // localhost only: never through a proxy
        cfg.waitsForConnectivity = false
        session = URLSession(configuration: cfg, delegate: self, delegateQueue: .main)
    }

    func start() { connect() }

    func close() {
        closing = true
        pingTimer?.invalidate()
        task?.cancel(with: .goingAway, reason: nil)
    }

    var isOpen: Bool { open }

    /// Sends one message if connected; returns whether it went out.
    @discardableResult
    func send<T: Encodable>(_ msg: T) -> Bool {
        guard open, let task else { return false }
        do {
            let json = try Wire.encode(msg)
            onSend?(json)
            task.send(.string(json)) { err in
                if let err { print("send failed: \(err.localizedDescription)") }
            }
        } catch {
            print("encode failed: \(error)")
            return false
        }
        return true
    }

    func sendHello() { send(helloProvider()) }

    // ---- connection
    private func connect() {
        guard !closing else { return }
        generation += 1
        let t = session.webSocketTask(with: url)
        t.maximumMessageSize = 16 << 20  // incoming: voice.audio chunks, plans
        task = t
        t.resume()
        receive(t, generation)
    }

    private func receive(_ t: URLSessionWebSocketTask, _ gen: Int) {
        t.receive { [weak self] result in
            DispatchQueue.main.async {
                guard let self, gen == self.generation else { return }
                switch result {
                case .success(let m):
                    let data: Data
                    switch m {
                    case .string(let s): data = Data(s.utf8)
                    case .data(let d): data = d
                    @unknown default: return
                    }
                    do { self.onMessage(try ServerMessage.decode(data)) } catch { print("bad message from server: \(error)") }
                    self.receive(t, gen)
                case .failure(let err):
                    self.dropped(gen, reason: err.localizedDescription)
                }
            }
        }
    }

    private func dropped(_ gen: Int, reason: String) {
        guard gen == generation, !closing else { return }
        generation += 1  // ignore any further callbacks of the dead task
        let wasOpen = open
        open = false
        pingTimer?.invalidate()
        task?.cancel(with: .abnormalClosure, reason: nil)
        if wasOpen { onConnected(false) }
        let delay = backoff.next(jitter: Double.random(in: 0...1))
        if wasOpen || !announcedOffline {
            print("not connected to \(url.absoluteString) (\(reason)); retrying in \(String(format: "%.1f", delay)) s, with backoff")
            announcedOffline = true
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + delay) { [weak self] in self?.connect() }
    }

    // ---- URLSessionWebSocketDelegate (main queue)
    func urlSession(_ session: URLSession, webSocketTask: URLSessionWebSocketTask, didOpenWithProtocol protocol: String?) {
        guard webSocketTask === task else { return }
        open = true
        announcedOffline = false
        backoff.reset()
        print("connected to \(url.absoluteString)")
        onConnected(true)
        sendHello()
        pingTimer?.invalidate()
        let gen = generation
        pingTimer = Timer.scheduledTimer(withTimeInterval: 15, repeats: true) { [weak self] _ in
            guard let self, gen == self.generation, let t = self.task else { return }
            t.sendPing { err in
                if let err { DispatchQueue.main.async { self.dropped(gen, reason: "ping: \(err.localizedDescription)") } }
            }
        }
    }

    func urlSession(
        _ session: URLSession, webSocketTask: URLSessionWebSocketTask,
        didCloseWith closeCode: URLSessionWebSocketTask.CloseCode, reason: Data?
    ) {
        guard webSocketTask === task else { return }
        let why = closeCode == .policyViolation ? "refused by the server: localhost only" : "closed (\(closeCode.rawValue))"
        dropped(generation, reason: why)
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        guard task === self.task, let error else { return }
        if let http = task.response as? HTTPURLResponse, http.statusCode == 403 {
            dropped(generation, reason: "HTTP 403: the server refused this helper (screen tutor is localhost only)")
        } else {
            dropped(generation, reason: error.localizedDescription)
        }
    }
}
