import AppKit
import ApplicationServices
import Carbon
import OverlayCore

/// Phase 12 on the helper side: what the tutor sees of the Mac.
/// - AX snapshots of the frontmost app: 300 ms after a focus/window change or a key, immediately after a click
///   (and again 300 ms later), and at least every 2 s while a walkthrough runs (5 s otherwise).
/// - Frames of the display under the frontmost window at `capture_fps`, plus one per click and per app/window change.
/// - Input events: clicks (with position) and keys (no content), from global monitors.
/// Nothing is read or captured while a blocklisted app is in front or a password field has focus; then the icon
/// goes capture-paused with the reason. Every callback runs on main.
final class Perception {
    var onSnapshot: (AXSnapshot) -> Void = { _ in }
    var onFrame: (ScreenFrame) -> Void = { _ in }
    var onInput: (InputEvent) -> Void = { _ in }
    var onBlock: (String?) -> Void = { _ in }
    var space: () -> ScreenSpace = { ScreenSpace(displays: []) }

    private(set) var enabled = false
    private(set) var blocklist: [String] = ["1Password", "Bitwarden", "Keychain Access"]  // until screen.config says
    private(set) var fps = 1.0
    private(set) var lastFrameAt = 0.0
    var guiding = false { didSet { if guiding != oldValue { scheduleAXTimer() } } }

    private let log: Bool
    private let ax = AXReader()
    private let capturer = ScreenCapturer()
    private var front: FrontApp?
    private var axTimer: Timer?
    private var frameTimer: Timer?
    private var snapItem: DispatchWorkItem?
    private var snapDue = 0.0
    private var snapRunning = false
    private var snapDirty = false
    private var lastSecure = false
    private var lastWindow: Rect?
    private var frameSeq = 0
    private var monitors: [Any] = []
    private var observer: AXObserver?
    private var observedPid: pid_t = 0
    private var lastKeySent = 0.0
    private var blockReason: String?
    private var trusted = false
    private var warned: Set<String> = []

    init(log: Bool) { self.log = log }

    func start() {
        let ws = NSWorkspace.shared.notificationCenter
        ws.addObserver(forName: NSWorkspace.didActivateApplicationNotification, object: nil, queue: .main) { [weak self] _ in
            self?.frontChanged()
        }
        // where the button went down: a drag (a field into "Filas") starts on the highlighted control and ends elsewhere
        if let m = NSEvent.addGlobalMonitorForEvents(matching: [.leftMouseDown], handler: { [weak self] _ in self?.pressAt = self?.pointer() }) { monitors.append(m) }
        if let m = NSEvent.addGlobalMonitorForEvents(matching: [.leftMouseUp], handler: { [weak self] _ in self?.clicked() }) { monitors.append(m) }
        if let m = NSEvent.addGlobalMonitorForEvents(matching: [.keyDown], handler: { [weak self] _ in self?.typed() }) { monitors.append(m) }
        trusted = AXIsProcessTrusted()
        frontChanged()
        scheduleAXTimer()
        scheduleFrameTimer()
    }

    /// Connected and not paused: send. Otherwise nothing is read, captured or sent.
    func setEnabled(_ on: Bool) {
        guard on != enabled else { return }
        enabled = on
        if on {
            snapshotNow()
            captureFrame(.focus)
        }
    }

    func configure(blocklist: [String], fps: Double) {
        self.blocklist = blocklist
        self.fps = min(max(fps, 0.1), 5)
        scheduleFrameTimer()
        updateBlock()
    }

    /// Accessibility may be granted while we run: start observing as soon as it is.
    func permissionsChanged() {
        let t = AXIsProcessTrusted()
        if t != trusted {
            trusted = t
            print("perception: accessibility \(t ? "allowed" : "not allowed")")
            if let f = front { observe(f.pid) }
            snapshotNow()
        }
    }

    /// A fresh look right now (a goal was just asked: the planner wants the current screen).
    func refreshNow() {
        snapshotNow()
        captureFrame(.focus)
    }

    var capturing: Bool { enabled && blockReason == nil && ProcessInfo.processInfo.systemUptime - lastFrameAt < 3 }

    // ---- triggers
    private func frontChanged() {
        guard let app = NSWorkspace.shared.frontmostApplication, app.processIdentifier != getpid() else { return }
        let f = FrontApp(pid: app.processIdentifier, name: app.localizedName ?? app.bundleIdentifier ?? "app", bundleId: app.bundleIdentifier ?? "")
        guard f != front else { return }
        front = f
        lastSecure = false
        lastWindow = nil
        capturer.invalidate()
        observe(f.pid)
        updateBlock()
        if log { print("perception: frontmost \(f.name) (\(f.bundleId))") }
        snapshot(after: 0.05)
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) { [weak self] in self?.captureFrame(.focus) }
    }

    private var pressAt: Point2?

    private func clicked() {
        guard enabled else { return }
        onInput(InputEvent(kind: .click, tMs: nowMs(), at: pressAt ?? pointer()))  // a drag counts where it started
        pressAt = nil
        snapshotNow()
        captureFrame(.click)
        snapshot(after: 0.3)
        snapshot(after: 0.8)  // after a drop the app takes a moment to update its controls
    }

    private func typed() {
        guard enabled else { return }
        let now = ProcessInfo.processInfo.systemUptime
        if now - lastKeySent >= 0.3 {  // coalesced: that the user typed, not how much
            lastKeySent = now
            onInput(InputEvent(kind: .key, tMs: nowMs()))
        }
        snapshot(after: 0.3)
    }

    fileprivate func axNotified(_ name: String) {
        snapshot(after: 0.3)
        if [kAXFocusedWindowChangedNotification, kAXWindowCreatedNotification, kAXMainWindowChangedNotification, "AXSheetCreated"].contains(name) {
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.35) { [weak self] in self?.captureFrame(.focus) }
        }
    }

    // ---- AX snapshots
    /// At most `after` seconds from now (an earlier request already pending wins: typing doesn't postpone it).
    private func snapshot(after: Double) {
        let due = ProcessInfo.processInfo.systemUptime + after
        if snapItem != nil, snapDue <= due { return }
        snapItem?.cancel()
        let item = DispatchWorkItem { [weak self] in
            self?.snapItem = nil
            self?.snapshotNow()
        }
        snapItem = item
        snapDue = due
        DispatchQueue.main.asyncAfter(deadline: .now() + after, execute: item)
    }

    private func snapshotNow() {
        guard enabled, let f = front else { return }
        let space = space()
        let ptr = pointer()
        if isBlocked(app: f.name, bundleId: f.bundleId, blocklist: blocklist) {
            onSnapshot(ax.blockedSnapshot(f, pointer: ptr, display: space.displays.first?.id ?? 0))
            return
        }
        guard trusted else {  // no Accessibility: the app's name is all the server gets
            warnOnce("ax", "perception: no Accessibility permission; AX snapshots carry the app name only")
            onSnapshot(AXSnapshot(app: f.name, bundleId: f.bundleId, display: space.displays.first?.id ?? 0,
                                  secureFieldFocused: IsSecureEventInputEnabled(), pointer: ptr, tMs: nowMs()))
            return
        }
        if snapRunning { snapDirty = true; return }
        snapRunning = true
        ax.snapshot(f, pointer: ptr, space: space) { [weak self] r in
            guard let self else { return }
            snapRunning = false
            guard f == front else { snapshot(after: 0.05); return }  // the user switched apps meanwhile
            lastSecure = r.snapshot.secureFieldFocused
            if let w = r.window { lastWindow = w }
            updateBlock()
            if enabled { onSnapshot(r.snapshot) }
            if r.ms > 100 || log {
                print("ax.snapshot \(f.name): \(r.snapshot.elements.count) elements (\(r.visited) visited\(r.truncated ? ", truncated" : "")) in \(String(format: "%.0f", r.ms)) ms\(r.ms > 100 ? "  (over the 100 ms budget)" : "")")
            }
            if snapDirty {
                snapDirty = false
                snapshot(after: 0.05)
            }
        }
    }

    private func scheduleAXTimer() {
        axTimer?.invalidate()
        let t = Timer(timeInterval: guiding ? 2 : 5, repeats: true) { [weak self] _ in self?.snapshot(after: 0) }
        RunLoop.main.add(t, forMode: .common)
        axTimer = t
    }

    // ---- frames
    private func scheduleFrameTimer() {
        frameTimer?.invalidate()
        let t = Timer(timeInterval: 1 / fps, repeats: true) { [weak self] _ in self?.captureFrame(.tick) }
        RunLoop.main.add(t, forMode: .common)
        frameTimer = t
    }

    private func captureFrame(_ trigger: FrameTrigger) {
        guard enabled, let f = front else { return }
        updateBlock()
        guard blockReason == nil else { return }
        guard CGPreflightScreenCaptureAccess() else {
            warnOnce("screen", "perception: no Screen Recording permission; no frames (AX and voice still work)")
            return
        }
        if capturer.busy {
            if trigger != .tick {  // a click / focus frame matters: take it right after the one in flight
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.15) { [weak self] in self?.captureFrame(trigger) }
            }
            return
        }
        let space = space()
        let window = frontWindowFrame(pid: f.pid) ?? lastWindow
        guard let display = window.flatMap({ space.display(for: $0) }) ?? space.displays.first else { return }
        let gf = space.globalFrame(display)
        capturer.capture(display: display, globalFrame: gf, blocklist: blocklist) { [weak self] result in
            guard let self else { return }
            switch result {
            case .success(let shot):
                updateBlock()
                guard enabled, blockReason == nil, f == front else { return }  // a password field got focus meanwhile
                frameSeq += 1
                lastFrameAt = ProcessInfo.processInfo.systemUptime
                let frame = ScreenFrame(
                    id: "f\(frameSeq)", display: display.id, origin: Point2(x: gf.x, y: gf.y), width: shot.width,
                    height: shot.height, scale: shot.scale, jpegB64: shot.jpeg.base64EncodedString(), window: window?.bbox,
                    tMs: nowMs(), trigger: trigger
                )
                onFrame(frame)
                if log { print("screen.frame \(frame.id) \(trigger.rawValue): \(shot.width)×\(shot.height) \(shot.jpeg.count / 1024) KB in \(String(format: "%.0f", shot.ms)) ms") }
            case .failure(let e):
                warnOnce("cap:\(e)", "perception: capture failed: \(e)")
            }
        }
    }

    // ---- capture pause: blocklisted app in front, or a password field focused
    private func updateBlock() {
        var why: String?
        if let f = front, isBlocked(app: f.name, bundleId: f.bundleId, blocklist: blocklist) {
            why = "Paused: \(f.name) is on the blocklist — nothing is captured"
        } else if lastSecure || IsSecureEventInputEnabled() {
            why = "Paused: a password field has focus — I'll wait"
        }
        if why != blockReason {
            blockReason = why
            onBlock(why)
        }
    }

    // ---- AX observer on the frontmost app
    private func observe(_ pid: pid_t) {
        if let o = observer {
            CFRunLoopRemoveSource(CFRunLoopGetMain(), AXObserverGetRunLoopSource(o), .commonModes)
            observer = nil
        }
        observedPid = 0
        guard trusted else { return }
        var obs: AXObserver?
        guard AXObserverCreate(pid, axCallback, &obs) == .success, let obs else { return }
        let appEl = AXUIElementCreateApplication(pid)
        let me = Unmanaged.passUnretained(self).toOpaque()
        for n in [kAXFocusedUIElementChangedNotification, kAXFocusedWindowChangedNotification, kAXMainWindowChangedNotification,
                  kAXWindowCreatedNotification, kAXMenuOpenedNotification, kAXMenuClosedNotification, "AXSheetCreated"] {
            AXObserverAddNotification(obs, appEl, n as CFString, me)
        }
        CFRunLoopAddSource(CFRunLoopGetMain(), AXObserverGetRunLoopSource(obs), .commonModes)
        observer = obs
        observedPid = pid
    }

    private func pointer() -> Point2 {
        let m = NSEvent.mouseLocation  // AppKit global: bottom-left of the main display, y up
        return Point2(x: Double(m.x), y: space().mainHeight - Double(m.y))
    }

    private func warnOnce(_ key: String, _ text: String) {
        if warned.insert(key).inserted { print(text) }
    }
}

private let axCallback: AXObserverCallback = { _, _, name, refcon in
    guard let refcon else { return }
    let me = Unmanaged<Perception>.fromOpaque(refcon).takeUnretainedValue()
    me.axNotified(name as String)
}
