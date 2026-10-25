import AppKit
import OverlayCore
import QuartzCore

/// What the menu shows, read fresh every time it opens.
struct MenuState {
    var connected = false
    var server = "127.0.0.1:8000"
    var listening = false
    var muted = false
    var busy = false  // planning, guiding or talking: "Cancel" is offered
    var paused = false
    var step = 0
    var of = 0
    var phaseText: String?
    var memoryNote: String?
    var missing: [Permission] = []
    var transcriptVisible = false
}

/// The phantom icon: a small round button that floats above everything (a separate, non-click-through panel), plus
/// the status bubble beside it. Drag it anywhere; click it for the menu.
final class PhantomController: NSObject {
    static let size = 64.0  // panel; the disc itself is 44 pt, the rest is room for the level ring and the pulse
    let panel: NSPanel
    let view: PhantomView
    let bubble = BubblePanel()
    let said = BubblePanel(above: true)  // "You said: …", above the icon
    let transcript = TranscriptPanel()

    var menuState: () -> MenuState = { MenuState() }
    var onListen: (Bool) -> Void = { _ in }
    var onMute: (Bool) -> Void = { _ in }
    var onControl: (GuideAction) -> Void = { _ in }
    var onPause: () -> Void = {}
    var onExit: () -> Void = {}
    var onPermission: (Permission) -> Void = { _ in }

    override init() {
        let s = Self.size
        panel = NSPanel(
            contentRect: NSRect(x: 0, y: 0, width: s, height: s), styleMask: [.borderless, .nonactivatingPanel],
            backing: .buffered, defer: false
        )
        view = PhantomView(frame: NSRect(x: 0, y: 0, width: s, height: s))
        super.init()
        panel.isOpaque = false
        panel.backgroundColor = .clear
        panel.hasShadow = false  // the disc draws its own
        panel.level = .screenSaver  // same level as the overlay, ordered above it
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary, .ignoresCycle]
        panel.isFloatingPanel = true
        panel.hidesOnDeactivate = false
        panel.becomesKeyOnlyIfNeeded = true
        panel.isReleasedWhenClosed = false
        panel.contentView = view
        view.onClick = { [weak self] in self?.showMenu() }
        view.onMoved = { [weak self] in self?.placeBubble() }
        if let main = NSScreen.screens.first {  // bottom-right of the main display, clear of the Dock
            let v = main.visibleFrame
            panel.setFrameOrigin(NSPoint(x: v.maxX - s - 16, y: v.minY + 16))
        }
        panel.orderFrontRegardless()
    }

    /// Icon state, live mic level (0…1), whether the mic is on and frames are flowing, and the bubble.
    func render(
        icon: IconState, level: Double, micOn: Bool, capturing: Bool, bubble b: Bubble?, said s: Bubble?, tooltip: String
    ) {
        view.update(state: icon, level: level, micOn: micOn, capturing: capturing)
        view.toolTip = tooltip
        bubble.show(b, near: panel.frame)
        said.show(s, near: panel.frame)
    }

    func setLevel(_ level: Double) { view.setLevel(level) }

    func bringToFront() {
        panel.orderFrontRegardless()
        bubble.raise()
        said.raise()
    }

    /// After a display change, pull the icon back if its display went away.
    func keepOnScreen() {
        let f = panel.frame
        if !NSScreen.screens.contains(where: { $0.frame.intersects(f) }), let v = NSScreen.screens.first?.visibleFrame {
            panel.setFrameOrigin(NSPoint(x: v.maxX - f.width - 16, y: v.minY + 16))
        }
        bringToFront()
        placeBubble()
    }

    private func placeBubble() {
        bubble.reposition(near: panel.frame)
        said.reposition(near: panel.frame)
    }

    // ---- menu
    private func showMenu() {
        let st = menuState()
        let m = NSMenu()
        m.autoenablesItems = false
        info(m, st.connected ? "Connected · \(st.server)" : "Not connected to \(st.server) — start the app server: make ipad")
        if let p = st.phaseText { info(m, p) }
        if st.of > 0 { info(m, st.step > 0 ? "Step \(st.step) of \(st.of)" : "\(st.of) steps planned") }
        if let mem = st.memoryNote { info(m, mem) }
        m.addItem(.separator())
        let listen = add(m, "Listen", #selector(toggleListen), enabled: true)  // you talk to it: no text box
        listen.state = st.listening ? .on : .off
        let mute = add(m, "Mute", #selector(toggleMute), enabled: true)
        mute.state = st.muted ? .on : .off
        if st.busy { add(m, "Cancel", #selector(stopGuide), enabled: true) }  // stop what it's doing, now
        if st.of > 0 {
            m.addItem(.separator())
            add(m, "Next step", #selector(next), enabled: st.connected)
            add(m, "Previous step", #selector(back), enabled: st.connected)
            add(m, "Repeat step", #selector(again), enabled: st.connected)
        }
        m.addItem(.separator())
        add(m, st.transcriptVisible ? "Hide transcript" : "Show transcript", #selector(toggleTranscript), enabled: true)
        add(m, st.paused ? "Resume" : "Pause", #selector(togglePause), enabled: true)
        if !st.missing.isEmpty {
            m.addItem(.separator())
            for p in st.missing {
                let i = add(m, p.menuTitle, #selector(permission(_:)), enabled: true)
                i.representedObject = p.rawValue
                i.toolTip = p.missingText
            }
        }
        m.addItem(.separator())
        add(m, "Exit", #selector(quitHelper), enabled: true)
        m.popUp(positioning: nil, at: NSPoint(x: 8, y: view.bounds.height - 4), in: view)
    }

    private func info(_ m: NSMenu, _ title: String) {
        let i = m.addItem(withTitle: title, action: nil, keyEquivalent: "")
        i.isEnabled = false
    }

    @discardableResult
    private func add(_ m: NSMenu, _ title: String, _ sel: Selector, enabled: Bool) -> NSMenuItem {
        let i = m.addItem(withTitle: title, action: sel, keyEquivalent: "")
        i.target = self
        i.isEnabled = enabled
        return i
    }

    @objc private func toggleListen() { onListen(!menuState().listening) }
    @objc private func toggleMute() { onMute(!menuState().muted) }
    @objc private func next() { onControl(.next) }
    @objc private func back() { onControl(.back) }
    @objc private func again() { onControl(.repeat) }
    @objc private func stopGuide() { onControl(.stop) }
    @objc private func toggleTranscript() { transcript.toggle() }
    @objc private func togglePause() { onPause() }
    @objc private func quitHelper() { onExit() }
    @objc private func permission(_ item: NSMenuItem) {
        if let raw = item.representedObject as? String, let p = Permission(rawValue: raw) { onPermission(p) }
    }
}

// ---------------------------------------------------------------- the icon
private struct Look {
    var fill: NSColor
    var symbol: String
}

private func look(_ s: IconState) -> Look {
    func c(_ r: Double, _ g: Double, _ b: Double) -> NSColor { NSColor(srgbRed: r, green: g, blue: b, alpha: 1) }
    switch s {
    case .idle: return Look(fill: c(0.20, 0.22, 0.27), symbol: "sparkles")
    case .listening: return Look(fill: c(0.10, 0.60, 0.36), symbol: "mic.fill")
    case .thinking: return Look(fill: c(0.92, 0.62, 0.10), symbol: "ellipsis")
    case .speaking: return Look(fill: Style.accent, symbol: "speaker.wave.2.fill")
    case .paused: return Look(fill: c(0.46, 0.47, 0.52), symbol: "pause.fill")
    case .capturePaused: return Look(fill: c(0.50, 0.33, 0.82), symbol: "eye.slash.fill")
    case .error: return Look(fill: c(0.83, 0.18, 0.18), symbol: "exclamationmark.triangle.fill")
    case .disconnected: return Look(fill: c(0.32, 0.32, 0.34), symbol: "bolt.slash.fill")
    }
}

/// The round button, drawn with layers so every state animates: idle breathes, listening shows the mic level as a
/// ring, thinking spins, speaking pulses, error flashes, disconnected blinks slowly. A green dot = frames are being
/// captured. A press that moves more than a few points is a drag; otherwise it's a click.
final class PhantomView: NSView {
    var onClick: () -> Void = {}
    var onMoved: () -> Void = {}
    private(set) var state: IconState?
    private let halo = CAShapeLayer()
    private let ring = CAShapeLayer()
    private let spinner = CAShapeLayer()
    private let disc = CAShapeLayer()
    private let symbol = CALayer()
    private let badge = CAShapeLayer()
    private var images: [String: CGImage] = [:]
    private var micOn = false
    private var downAt: NSPoint?
    private var origin: NSPoint = .zero
    private var dragged = false

    override init(frame: NSRect) {
        super.init(frame: frame)
        wantsLayer = true
        let root = layer!
        let c = CGPoint(x: bounds.midX, y: bounds.midY)
        func circle(_ l: CAShapeLayer, _ r: CGFloat) {
            l.bounds = CGRect(x: 0, y: 0, width: 2 * r, height: 2 * r)
            l.position = c
            l.path = CGPath(ellipseIn: l.bounds, transform: nil)
        }
        circle(halo, 22)
        halo.opacity = 0
        circle(ring, 26)
        ring.fillColor = nil
        ring.strokeColor = NSColor(srgbRed: 0.25, green: 0.85, blue: 0.50, alpha: 1).cgColor
        ring.lineWidth = 2
        ring.opacity = 0
        spinner.bounds = CGRect(x: 0, y: 0, width: 52, height: 52)
        spinner.position = c
        spinner.path = CGPath(ellipseIn: spinner.bounds.insetBy(dx: 1.5, dy: 1.5), transform: nil)
        spinner.fillColor = nil
        spinner.strokeColor = NSColor.white.withAlphaComponent(0.95).cgColor
        spinner.lineWidth = 3
        spinner.lineCap = .round
        spinner.strokeStart = 0
        spinner.strokeEnd = 0.3
        spinner.opacity = 0
        circle(disc, 22)
        disc.lineWidth = 2
        disc.shadowColor = NSColor.black.cgColor
        disc.shadowOpacity = 0.35
        disc.shadowRadius = 4
        disc.shadowOffset = CGSize(width: 0, height: -1)
        symbol.bounds = CGRect(x: 0, y: 0, width: 24, height: 24)
        symbol.position = c
        symbol.contentsGravity = .resizeAspect
        badge.bounds = CGRect(x: 0, y: 0, width: 10, height: 10)
        badge.path = CGPath(ellipseIn: badge.bounds, transform: nil)
        badge.position = CGPoint(x: c.x + 16, y: c.y + 16)
        badge.fillColor = NSColor(srgbRed: 0.20, green: 0.85, blue: 0.40, alpha: 1).cgColor
        badge.strokeColor = NSColor.white.cgColor
        badge.lineWidth = 1.5
        badge.opacity = 0
        for l in [halo, ring, spinner, disc, symbol, badge] { root.addSublayer(l) }
        let scale = NSScreen.main?.backingScaleFactor ?? 2
        for l in [halo, ring, spinner, disc, symbol, badge] { l.contentsScale = scale }
    }

    required init?(coder: NSCoder) { fatalError() }

    func update(state s: IconState, level: Double, micOn on: Bool, capturing: Bool) {
        micOn = on
        CATransaction.begin()
        CATransaction.setAnimationDuration(0.25)
        badge.opacity = capturing ? 1 : 0
        if s != state {
            state = s
            restyle(s)
        }
        CATransaction.commit()
        setLevel(on ? level : 0)
    }

    /// The mic level ring (0…1), ~10 times a second while Listen is on.
    func setLevel(_ level: Double) {
        CATransaction.begin()
        CATransaction.setAnimationDuration(0.12)
        let l = micOn ? max(0, min(1, level)) : 0
        ring.opacity = micOn ? Float(0.45 + 0.55 * l) : 0
        ring.lineWidth = CGFloat(2 + 5 * l)
        ring.transform = CATransform3DMakeScale(CGFloat(0.96 + 0.16 * l), CGFloat(0.96 + 0.16 * l), 1)
        CATransaction.commit()
    }

    private func restyle(_ s: IconState) {
        let lk = look(s)
        disc.fillColor = lk.fill.withAlphaComponent(s == .disconnected ? 0.85 : 0.95).cgColor
        disc.strokeColor = (s == .disconnected ? NSColor.white.withAlphaComponent(0.55) : NSColor.white.withAlphaComponent(0.85)).cgColor
        disc.lineDashPattern = s == .disconnected ? [3, 3] : nil
        symbol.contents = image(lk.symbol)
        halo.fillColor = lk.fill.cgColor
        for l in [disc, halo, spinner, symbol] { l.removeAllAnimations() }
        spinner.opacity = 0
        halo.opacity = 0
        disc.opacity = 1
        switch s {
        case .idle:
            disc.add(breathe(from: 0.80, to: 1, period: 2.4), forKey: "breathe")
        case .listening:
            disc.add(breathe(from: 0.88, to: 1, period: 1.6), forKey: "breathe")
        case .thinking:
            spinner.opacity = 1
            let spin = CABasicAnimation(keyPath: "transform.rotation.z")
            spin.fromValue = 0
            spin.toValue = -2 * Double.pi  // clockwise on screen
            spin.duration = 0.9
            spin.repeatCount = .infinity
            spinner.add(spin, forKey: "spin")
        case .speaking:
            halo.add(pulse(scale: 1.45, period: 0.9), forKey: "pulse")
        case .paused, .capturePaused:
            disc.add(breathe(from: 0.75, to: 0.95, period: 3.2), forKey: "breathe")
        case .error:
            halo.add(pulse(scale: 1.35, period: 1.4), forKey: "pulse")
            let shake = CAKeyframeAnimation(keyPath: "position.x")
            let x = bounds.midX
            shake.values = [x, x - 4, x + 4, x - 3, x + 3, x]
            shake.duration = 0.4
            symbol.add(shake, forKey: "shake")
        case .disconnected:
            disc.add(breathe(from: 0.45, to: 0.9, period: 1.2), forKey: "blink")
        }
    }

    private func breathe(from: Double, to: Double, period: Double) -> CAAnimation {
        let a = CABasicAnimation(keyPath: "opacity")
        a.fromValue = from
        a.toValue = to
        a.duration = period / 2
        a.autoreverses = true
        a.repeatCount = .infinity
        a.timingFunction = CAMediaTimingFunction(name: .easeInEaseOut)
        return a
    }

    private func pulse(scale: Double, period: Double) -> CAAnimation {
        let grow = CABasicAnimation(keyPath: "transform.scale")
        grow.fromValue = 1
        grow.toValue = scale
        let fade = CABasicAnimation(keyPath: "opacity")
        fade.fromValue = 0.7
        fade.toValue = 0
        let g = CAAnimationGroup()
        g.animations = [grow, fade]
        g.duration = period
        g.repeatCount = .infinity
        g.timingFunction = CAMediaTimingFunction(name: .easeOut)
        return g
    }

    private func image(_ name: String) -> CGImage? {
        if let i = images[name] { return i }
        let cfg = NSImage.SymbolConfiguration(pointSize: 18, weight: .semibold)
        guard let img = (NSImage(systemSymbolName: name, accessibilityDescription: "Ink Tutor")
            ?? NSImage(systemSymbolName: "sparkles", accessibilityDescription: "Ink Tutor"))?.withSymbolConfiguration(cfg)
        else { return nil }
        let scale = NSScreen.main?.backingScaleFactor ?? 2
        let px = NSSize(width: 24 * scale, height: 24 * scale)
        let tinted = NSImage(size: px, flipped: false) { rect in
            let s = img.size
            let k = min(rect.width / s.width, rect.height / s.height)
            let r = NSRect(x: (rect.width - s.width * k) / 2, y: (rect.height - s.height * k) / 2, width: s.width * k, height: s.height * k)
            img.draw(in: r)
            NSColor.white.set()
            r.fill(using: .sourceAtop)
            return true
        }
        let cg = tinted.cgImage(forProposedRect: nil, context: nil, hints: nil)
        images[name] = cg
        return cg
    }

    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }

    override func mouseDown(with event: NSEvent) {
        downAt = NSEvent.mouseLocation
        origin = window?.frame.origin ?? .zero
        dragged = false
    }

    override func mouseDragged(with event: NSEvent) {
        guard let start = downAt, let w = window else { return }
        let p = NSEvent.mouseLocation
        let dx = p.x - start.x, dy = p.y - start.y
        if !dragged && dx * dx + dy * dy < 9 { return }
        dragged = true
        w.setFrameOrigin(NSPoint(x: origin.x + dx, y: origin.y + dy))
        onMoved()
    }

    override func mouseUp(with event: NSEvent) {
        defer { downAt = nil }
        if !dragged { onClick() }
    }
}

// ---------------------------------------------------------------- the status bubble
/// A dark rounded bubble beside the icon (left of it, or right when there's no room): what the tutor is doing,
/// what it heard, errors. Click-through; fades in and out.
final class BubblePanel {
    private let panel: NSPanel
    private let box = NSView()
    private let label = NSTextField(wrappingLabelWithString: "")
    private var current: Bubble?
    private var size = NSSize.zero
    private let above: Bool
    static let maxWidth = 300.0
    static let pad = (x: 12.0, y: 8.0)

    /// `above`: sits above the icon (right-aligned to it) instead of beside it.
    init(above: Bool = false) {
        self.above = above
        panel = NSPanel(contentRect: NSRect(x: 0, y: 0, width: 10, height: 10), styleMask: [.borderless, .nonactivatingPanel], backing: .buffered, defer: false)
        panel.isOpaque = false
        panel.backgroundColor = .clear
        panel.hasShadow = true
        panel.level = .screenSaver
        panel.ignoresMouseEvents = true
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary, .ignoresCycle]
        panel.hidesOnDeactivate = false
        panel.isReleasedWhenClosed = false
        panel.alphaValue = 0
        box.wantsLayer = true
        box.layer?.cornerRadius = 10
        box.layer?.borderWidth = 1.5
        label.font = NSFont.systemFont(ofSize: 13, weight: .medium)
        label.textColor = .white
        label.maximumNumberOfLines = 6
        label.lineBreakMode = .byWordWrapping
        box.addSubview(label)
        panel.contentView = box
    }

    func raise() { if current != nil { panel.orderFrontRegardless() } }

    func show(_ b: Bubble?, near icon: NSRect) {
        guard b?.text != current?.text || b?.kind != current?.kind else { return }
        current = b
        guard let b else {
            NSAnimationContext.runAnimationGroup({ ctx in
                ctx.duration = 0.25
                panel.animator().alphaValue = 0
            }, completionHandler: { [weak self] in if self?.current == nil { self?.panel.orderOut(nil) } })
            return
        }
        let border: NSColor
        switch b.kind {
        case .info: border = NSColor.white.withAlphaComponent(0.35)
        case .heard: border = NSColor(srgbRed: 0.25, green: 0.85, blue: 0.50, alpha: 1)
        case .step: border = Style.accent
        case .error: border = NSColor(srgbRed: 0.95, green: 0.30, blue: 0.30, alpha: 1)
        }
        box.layer?.backgroundColor = Style.captionBG.cgColor
        box.layer?.borderColor = border.cgColor
        label.stringValue = b.text
        let attr = NSAttributedString(string: b.text, attributes: [.font: label.font!])
        let r = attr.boundingRect(with: NSSize(width: Self.maxWidth, height: 10_000), options: [.usesLineFragmentOrigin, .usesFontLeading])
        let tw = ceil(r.width) + 4, th = min(ceil(r.height) + 2, 6 * 18)
        size = NSSize(width: tw + 2 * Self.pad.x, height: th + 2 * Self.pad.y)
        label.frame = NSRect(x: Self.pad.x, y: Self.pad.y, width: tw, height: th)
        reposition(near: icon)
        panel.orderFrontRegardless()
        NSAnimationContext.runAnimationGroup { ctx in
            ctx.duration = 0.15
            panel.animator().alphaValue = 1
        }
    }

    func reposition(near icon: NSRect) {
        guard size.width > 0 else { return }
        let screen = NSScreen.screens.first(where: { $0.frame.intersects(icon) }) ?? NSScreen.main
        let vis = screen?.visibleFrame ?? icon.insetBy(dx: -1000, dy: -1000)
        var x = above ? icon.maxX - size.width : icon.minX - 6 - size.width
        if x < vis.minX + 8 { x = above ? icon.minX : icon.maxX + 6 }
        var y = above ? icon.maxY + 8 : icon.midY - size.height / 2
        if above && y + size.height > vis.maxY - 8 { y = icon.minY - 8 - size.height }  // no room above: below
        y = min(max(y, vis.minY + 8), vis.maxY - 8 - size.height)
        x = min(max(x, vis.minX + 8), vis.maxX - 8 - size.width)
        panel.setFrame(NSRect(x: x, y: y, width: size.width, height: size.height), display: true)
    }
}

// ---------------------------------------------------------------- the transcript panel
/// The conversation log: you / tutor / step / status lines with times, newest last, scrolled to the end.
final class TranscriptPanel {
    private let panel: NSPanel
    private let text: NSTextView

    init() {
        panel = NSPanel(
            contentRect: NSRect(x: 0, y: 0, width: 400, height: 320),
            styleMask: [.titled, .closable, .resizable, .utilityWindow, .nonactivatingPanel], backing: .buffered, defer: true
        )
        panel.title = "Ink Tutor — transcript"
        panel.level = .screenSaver
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        panel.isReleasedWhenClosed = false
        panel.hidesOnDeactivate = false
        let scroll = NSScrollView(frame: panel.contentView!.bounds)
        scroll.autoresizingMask = [.width, .height]
        scroll.hasVerticalScroller = true
        text = NSTextView(frame: scroll.bounds)
        text.isEditable = false
        text.autoresizingMask = [.width]
        text.textContainerInset = NSSize(width: 8, height: 8)
        scroll.documentView = text
        panel.contentView = scroll
        if let v = NSScreen.screens.first?.visibleFrame {
            panel.setFrameOrigin(NSPoint(x: v.maxX - 400 - 24, y: v.minY + 100))
        }
    }

    var isVisible: Bool { panel.isVisible }

    func toggle() { if panel.isVisible { panel.orderOut(nil) } else { panel.orderFrontRegardless() } }

    func render(_ log: TranscriptLog) {
        let out = NSMutableAttributedString()
        let small = NSFont.monospacedDigitSystemFont(ofSize: 11, weight: .regular)
        let body = NSFont.systemFont(ofSize: 13)
        for l in log.lines {
            let colour: NSColor
            switch l.who {
            case .you: colour = .systemBlue
            case .tutor: colour = Style.accent
            case .step: colour = .systemPurple
            case .status: colour = .secondaryLabelColor
            case .error: colour = .systemRed
            }
            out.append(NSAttributedString(string: TranscriptLog.clock(l.t) + "  ", attributes: [.font: small, .foregroundColor: NSColor.tertiaryLabelColor]))
            out.append(NSAttributedString(string: "\(l.who.rawValue): ", attributes: [.font: NSFont.boldSystemFont(ofSize: 13), .foregroundColor: colour]))
            var attrs: [NSAttributedString.Key: Any] = [.font: body, .foregroundColor: l.who == .status ? NSColor.secondaryLabelColor : NSColor.labelColor]
            if l.partial {
                attrs[.font] = NSFontManager.shared.convert(body, toHaveTrait: .italicFontMask)
                attrs[.foregroundColor] = NSColor.secondaryLabelColor
            }
            out.append(NSAttributedString(string: l.text + (l.partial ? " …" : "") + "\n", attributes: attrs))
        }
        text.textStorage?.setAttributedString(out)
        text.scrollToEndOfDocument(nil)
    }
}
