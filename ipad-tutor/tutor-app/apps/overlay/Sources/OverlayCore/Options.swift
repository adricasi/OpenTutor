import Foundation

public let helperName = "inktutor-overlay 0.1.0"

public struct OptionError: Error, CustomStringConvertible {
    public var description: String
    public init(_ d: String) { description = d }
}

/// Command line: `inktutor-overlay [--server 127.0.0.1:8000] [--session mac] [--log-frames] [--record DIR] …`.
public struct Options: Equatable {
    public var server = "127.0.0.1:8000"
    public var session = "mac"
    public var logFrames = false
    public var logAX = false
    public var record: String?
    public var prompts = true  // ask for Screen Recording / Accessibility at launch when missing
    public var aec = false  // macOS voice processing (echo cancellation): opt-in, it gives silence on some Macs
    public var capture = true
    public var listenAtStart = true  // you just talk to it: Listen turns on when it first connects
    public var smoke = false  // dev: drives Ask and Listen by itself and prints what the icon shows
    public var help = false

    public init() {}

    public static let usage = """
        usage: inktutor-overlay [--server HOST:PORT | PORT | ws://HOST:PORT] [--session ID] [--log-frames] [--log-ax]
                                [--record DIR] [--no-prompts] [--aec] [--no-capture] [--no-listen]
          --server      the Ink Tutor server, localhost only (default 127.0.0.1:8000)
          --session     screen session id: connects to /ws/screen/<ID> (default mac)
          --log-frames  print every overlay window frame and every drawn box (global and window coordinates)
          --log-ax      print every AX snapshot / screen frame sent (sizes and timings, no content)
          --record DIR  write every message the helper sends to DIR/messages.jsonl, frames as DIR/<id>.jpg
          --no-prompts  don't ask for Screen Recording / Accessibility at launch (the menu still offers it)
          --aec         try macOS echo cancellation on the mic (experimental; falls back by itself if the mic stays
                        silent). Without it the mic is muted towards the server while the tutor talks; barge-in still works
          --no-capture  no screen frames and no AX snapshots (voice and highlights only)
          --no-listen   don't turn Listen on when it connects (turn it on from the menu)
          --smoke       dev check: 3 s after launch asks a test goal, then turns Listen on for 6 s and off
        """

    public static func parse(_ args: [String]) throws -> Options {
        var o = Options()
        var it = args.makeIterator()
        func value(_ flag: String) throws -> String {
            guard let v = it.next(), !v.hasPrefix("--") else { throw OptionError("\(flag) needs a value") }
            return v
        }
        while let a = it.next() {
            switch a {
            case "--server": o.server = try value(a)
            case "--session": o.session = try value(a)
            case "--log-frames": o.logFrames = true
            case "--log-ax": o.logAX = true
            case "--record": o.record = try value(a)
            case "--no-prompts": o.prompts = false
            case "--aec": o.aec = true
            case "--no-capture": o.capture = false
            case "--no-listen": o.listenAtStart = false
            case "--smoke": o.smoke = true
            case "-h", "--help": o.help = true
            default:
                if a.hasPrefix("--server=") { o.server = String(a.dropFirst(9)) }
                else if a.hasPrefix("--session=") { o.session = String(a.dropFirst(10)) }
                else if a.hasPrefix("--record=") { o.record = String(a.dropFirst(9)) }
                else if a.hasPrefix("-psn_") { continue }  // added by Finder/`open` on old systems
                else { throw OptionError("unknown option \(a)") }
            }
        }
        _ = try o.url()
        if let r = o.record, r.isEmpty { throw OptionError("--record needs a folder") }
        return o
    }

    /// "127.0.0.1:8000", for messages.
    public var serverLabel: String {
        guard let u = try? url() else { return server }
        return "\(u.host ?? "127.0.0.1"):\(u.port ?? 8000)"
    }

    /// `ws://<host>:<port>/ws/screen/<session>`. The screen tutor is localhost only, so other hosts are refused.
    public func url() throws -> URL {
        guard session.range(of: #"^[A-Za-z0-9_.-]{1,64}$"#, options: .regularExpression) != nil else {
            throw OptionError("bad --session \(session): use letters, digits, '.', '_' or '-'")
        }
        var s = server.trimmingCharacters(in: .whitespaces)
        if s.allSatisfy(\.isNumber) { s = "127.0.0.1:\(s)" }
        if s.hasPrefix(":") { s = "127.0.0.1\(s)" }
        if !s.contains("://") { s = "ws://\(s)" }
        guard var c = URLComponents(string: s), let host = c.host?.lowercased() else {
            throw OptionError("bad --server \(server)")
        }
        switch c.scheme?.lowercased() {
        case "ws", "http": c.scheme = "ws"
        default: throw OptionError("--server must be ws:// or http:// on localhost (got \(server))")
        }
        guard ["127.0.0.1", "localhost", "::1", "[::1]"].contains(host) else {
            throw OptionError("--server must be localhost: screen data never leaves the Mac (got \(host))")
        }
        if c.port == nil { c.port = 8000 }
        c.path = "/ws/screen/\(session)"
        c.query = nil
        guard let url = c.url else { throw OptionError("bad --server \(server)") }
        return url
    }
}

/// Reconnect delays: 0.5 s, 1, 2, 4, 8, then 10 s; `jitter` (0…1) spreads them by up to ±20 %.
public struct Backoff: Equatable {
    public var base = 0.5
    public var cap = 10.0
    public private(set) var attempt = 0

    public init() {}

    public mutating func next(jitter: Double = 0.5) -> Double {
        let d = min(cap, base * pow(2, Double(attempt)))
        attempt += 1
        return d * (0.8 + 0.4 * min(max(jitter, 0), 1))
    }

    public mutating func reset() { attempt = 0 }
}
