import Foundation

/// What the phantom icon shows. Wider than the protocol's `PhantomState` (which the server can set too): the helper
/// also knows when it is disconnected or something failed, and the icon must never look dead.
public enum IconState: String, Equatable, Sendable, CaseIterable {
    case idle, listening, thinking, speaking, paused, capturePaused, error, disconnected
}

/// The status bubble next to the icon.
public struct Bubble: Equatable, Sendable {
    public enum Kind: String, Equatable, Sendable { case info, heard, error, step }
    public var text: String
    public var kind: Kind
    public var hideAt: Double?  // nil: stays until replaced

    public init(_ text: String, _ kind: Kind = .info, hideAt: Double? = nil) { self.text = text; self.kind = kind; self.hideAt = hideAt }
}

/// The helper's live status: every input (socket, Ask, Listen, guide.status, transcripts, playback, capture
/// pauses, errors) goes in; the icon state and the bubble come out. Pure, with the clock passed in, so
/// overlay-selftest can drive it (ask → thinking → 5 s timeout message, guide phases → icon + text).
public struct StatusModel: Equatable, Sendable {
    public static let replyTimeout = 5.0  // Ask / an utterance with no guide.status or guide.plan by then → say so
    public static let giveUp = 45.0  // stop spinning after this long without any reply
    public static let infoTtl = 4.0
    public static let errorTtl = 10.0

    public var server: String  // "127.0.0.1:8000", for the messages
    public private(set) var connected = false
    public private(set) var listening = false
    public private(set) var speaking = false
    public private(set) var paused = false
    public private(set) var captureBlock: String?  // why frames are skipped (blocklisted app, password field)
    public private(set) var phase: GuidePhase = .idle
    public private(set) var step = 0
    public private(set) var of = 0
    public private(set) var waitingSince: Double?  // a goal / utterance went out, no reply yet
    public private(set) var timedOut = false
    public private(set) var errorUntil: Double?
    public private(set) var serverState: PhantomState?  // overlay phantom_state
    public private(set) var bubble: Bubble?
    public private(set) var connectingSince: Double?

    public init(server: String) { self.server = server }

    /// At launch: "Connecting…", then (if the server still isn't there after 2 s) the not-connected message.
    public mutating func starting(now: Double) {
        connectingSince = now
        show("Connecting to the app server (\(server))…")
    }

    public var notConnectedText: String { "Not connected to the app server (\(server))" }
    public static let sentText = "Sent — waiting for the tutor…"
    public static let noAnswerText = "No answer from the tutor yet (is the model loaded?)"
    public static let listeningText = "Listening… say what you want to do"

    /// Whether a walkthrough is under way (Next / Back / Repeat / Stop make sense).
    // step 1 is shown while the rest of the plan still streams in (of = 0 until guide.plan arrives)
    public var guiding: Bool { (of > 0 || step > 0) && ![.idle, .done, .error].contains(phase) }

    // ---- what the icon shows (first match wins)
    public func icon(now: Double) -> IconState {
        if !connected { return .disconnected }
        if let e = errorUntil, now < e { return .error }
        if paused || phase == .paused || serverState == .paused { return .paused }
        if speaking || serverState == .speaking { return .speaking }
        if waitingSince != nil || [.planning, .locating, .replanning].contains(phase) || serverState == .thinking { return .thinking }
        if captureBlock != nil || serverState == .capturePaused { return .capturePaused }
        if listening { return .listening }
        return .idle
    }

    /// "You said: …": the last sentence the recognizer heard, in its own bubble so the status bubble ("Got it —
    /// planning…") doesn't replace it before you can check it. Shown for `saidTtl` or until the next sentence.
    public static let saidTtl = 10.0
    public struct Said: Equatable, Sendable {
        public var text: String
        public var at: Double
    }

    public private(set) var youSaid: Said?

    public func visibleYouSaid(now: Double) -> Bubble? {
        guard let s = youSaid, now - s.at < Self.saidTtl else { return nil }
        return Bubble("You said: “\(s.text)”", .heard)
    }

    /// The bubble, if it is showing at `now`.
    public func visibleBubble(now: Double) -> Bubble? {
        guard let b = bubble else { return nil }
        if let t = b.hideAt, now >= t { return nil }
        return b
    }

    // ---- inputs
    public mutating func setConnected(_ on: Bool, now: Double) {
        guard on != connected else { return }
        connected = on
        connectingSince = nil
        if on {
            show("Connected to the tutor", hideAt: now + 2.5)
        } else {
            waitingSince = nil
            timedOut = false
            speaking = false
            show(notConnectedText, .error)
        }
    }

    /// "Ask…" sent a goal: thinking at once, with a timeout message if nothing comes back.
    public mutating func askSent(now: Double) {
        guard connected else {
            show(notConnectedText + " — start it with make ipad", .error, hideAt: now + Self.errorTtl)
            return
        }
        waitingSince = now
        timedOut = false
        show(Self.sentText)
    }

    public mutating func setListening(_ on: Bool, now: Double) {
        listening = on
        if on {
            if connected { show(Self.listeningText, hideAt: now + Self.infoTtl) } else { show(notConnectedText + " — listening resumes when it's back", .error) }
        } else if bubble?.kind == .heard || bubble?.text == Self.listeningText {
            bubble = nil
        }
    }

    /// The mic can't start (no permission, no input device).
    public mutating func micProblem(_ text: String, now: Double) {
        listening = false
        show(text, .error, hideAt: now + Self.errorTtl * 1.5)
        errorUntil = now + 3
    }

    public mutating func setPaused(_ on: Bool, now: Double) {
        paused = on
        show(on ? "Paused — the tutor isn't watching or listening" : "Resumed", hideAt: now + Self.infoTtl)
    }

    public mutating func setCaptureBlock(_ why: String?, now: Double) {
        guard why != captureBlock else { return }
        captureBlock = why
        if let why { show(why, hideAt: now + 6) } else if bubble?.kind == .info, bubble?.text.hasPrefix("Paused:") == true { bubble = nil }
    }

    public mutating func setSpeaking(_ on: Bool, now: Double) {
        speaking = on
        if on { gotReply() }
    }

    public mutating func serverPhantom(_ s: PhantomState) { serverState = s == .idle || s == .listening ? nil : s }

    /// What the speech recognizer hears: partial lines show live; a final one is a question → thinking.
    public mutating func heard(_ text: String, final: Bool, now: Double) {
        let t = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !t.isEmpty else { return }
        if final {
            youSaid = Said(text: t, at: now)
            show("You: \(t)", .heard)
            waitingSince = now
            timedOut = false
        } else {
            show("“\(t)…”", .heard)
        }
    }

    public mutating func planArrived(steps: Int, now: Double) {
        gotReply()
        of = steps
        if phase == .idle || phase == .done || phase == .error { show("Plan ready: \(steps) step\(steps == 1 ? "" : "s")", .step) }
    }

    public mutating func guideStatus(_ m: GuideStatusMsg, now: Double) {
        gotReply()
        phase = m.phase
        step = m.step
        if m.of > 0 { of = m.of }
        let d = m.detail.trimmingCharacters(in: .whitespacesAndNewlines)
        switch m.phase {
        case .idle:
            of = 0
            if d.isEmpty { bubble = nil } else { show(d, hideAt: now + Self.infoTtl) }
        case .planning: show(d.isEmpty ? "Got it — planning…" : d)
        case .locating: show(stepText(d.isEmpty ? "looking for it on screen…" : d), .step)
        case .guiding: show(stepText(d), .step)
        case .verifying: show(d.isEmpty ? stepText("waiting for you…") : d, .step)
        case .replanning: show(d.isEmpty ? "Something changed — replanning…" : d)
        case .paused: show(d.isEmpty ? "Walkthrough paused" : d)
        case .done:
            show(d.isEmpty ? "Done!" : d, hideAt: now + Self.infoTtl + 2)
        case .error:
            errorUntil = now + Self.errorTtl
            show(d.isEmpty ? "The tutor hit a problem" : d, .error, hideAt: now + Self.errorTtl * 2)
        }
    }

    public mutating func serverError(_ message: String, now: Double) {
        gotReply()
        if [.planning, .locating, .replanning].contains(phase) { phase = .idle }  // whatever it was doing, it stopped
        errorUntil = now + 4
        show(message, .error, hideAt: now + Self.errorTtl)
    }

    /// Something to say for a moment (permissions, recorder, …).
    public mutating func note(_ text: String, error: Bool = false, now: Double, ttl: Double = StatusModel.infoTtl) {
        show(text, error ? .error : .info, hideAt: now + ttl)
        if error { errorUntil = now + 3 }
    }

    /// Call a few times a second: timeouts and auto-hide.
    public mutating func tick(now: Double) {
        if !connected, let s = connectingSince, now - s >= 2 {
            connectingSince = nil
            show(notConnectedText, .error)
        }
        if let t = waitingSince {
            if !timedOut, now - t >= Self.replyTimeout {
                timedOut = true
                show(Self.noAnswerText, .error)
            }
            if now - t >= Self.giveUp {
                waitingSince = nil
                show(Self.noAnswerText, .error, hideAt: now + Self.errorTtl)
            }
        }
        if let b = bubble, let h = b.hideAt, now >= h { bubble = nil }
        if let e = errorUntil, now >= e { errorUntil = nil }
    }

    // ---- helpers
    private func stepText(_ d: String) -> String {
        guard step > 0 else { return d.isEmpty ? "Working on it…" : d }
        if d.lowercased().hasPrefix("step ") { return d }
        let n = of > 0 ? "Step \(step) of \(of)" : "Step \(step)"  // the total is known once the whole plan arrived
        return d.isEmpty ? n : "\(n): \(d)"
    }

    private mutating func gotReply() {
        waitingSince = nil
        timedOut = false
    }

    private mutating func show(_ text: String, _ kind: Bubble.Kind = .info, hideAt: Double? = nil) {
        bubble = Bubble(text, kind, hideAt: hideAt)
    }
}

/// The transcript panel's log: you / tutor / step / status lines with times. A partial "you" line is replaced in
/// place until its final version arrives; a status line equal to the previous one isn't repeated.
public struct TranscriptLog: Equatable, Sendable {
    public enum Who: String, Equatable, Sendable { case you = "You", tutor = "Tutor", step = "Step", status = "Status", error = "Error" }
    public struct Line: Equatable, Sendable {
        public var t: Double  // seconds since 1970
        public var who: Who
        public var text: String
        public var partial: Bool
    }

    public private(set) var lines: [Line] = []
    public var limit = 500

    public init() {}

    /// Returns true if the last line was replaced rather than a new one added.
    @discardableResult
    public mutating func add(_ who: Who, _ text: String, t: Double, partial: Bool = false) -> Bool {
        let s = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !s.isEmpty else { return false }
        if let last = lines.last {
            if last.who == .you && last.partial && who == .you {
                lines[lines.count - 1] = Line(t: t, who: who, text: s, partial: partial)
                return true
            }
            if who == .status && last.who == .status && last.text == s { return true }
        }
        lines.append(Line(t: t, who: who, text: s, partial: partial))
        if lines.count > limit { lines.removeFirst(lines.count - limit) }
        return false
    }

    /// A partial "you" line that never got its final version (the speaker stopped mid-word): drop it.
    public mutating func dropPartial() {
        if let last = lines.last, last.partial { lines.removeLast() }
    }

    public static func clock(_ t: Double) -> String {
        let f = DateFormatter()
        f.dateFormat = "HH:mm:ss"
        return f.string(from: Date(timeIntervalSince1970: t))
    }
}
