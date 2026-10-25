import AppKit
import OverlayCore
import QuartzCore

enum Style {
    static let accent = NSColor(srgbRed: 1.0, green: 0.42, blue: 0.17, alpha: 1)  // the tutor's colour: warm orange
    static let captionBG = NSColor(srgbRed: 0.10, green: 0.11, blue: 0.13, alpha: 0.88)
    static let captionFont = NSFont.systemFont(ofSize: 15, weight: .medium)
    static let captionMaxWidth = 360.0
    static let captionPad = (x: 12.0, y: 8.0)
}

/// A borderless, transparent, click-through window covering one display, above normal and full-screen apps.
final class OverlayWindow: NSWindow {
    let display: Display

    init(screen: NSScreen, display: Display) {
        self.display = display
        super.init(contentRect: screen.frame, styleMask: .borderless, backing: .buffered, defer: false)
        setFrame(screen.frame, display: false)
        isOpaque = false
        backgroundColor = .clear
        hasShadow = false
        ignoresMouseEvents = true  // click-through: the user's clicks go to the app underneath
        level = .screenSaver  // above normal windows, the Dock, the menu bar and full-screen spaces
        collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary, .ignoresCycle]
        isReleasedWhenClosed = false
        animationBehavior = .none
        let view = NSView(frame: NSRect(origin: .zero, size: screen.frame.size))
        view.wantsLayer = true
        contentView = view
    }

    override var canBecomeKey: Bool { false }
    override var canBecomeMain: Bool { false }

    var root: CALayer { contentView!.layer! }
}

/// Owns one OverlayWindow per display and draws the OverlayStore into all of them: every window draws every event
/// in its own view coordinates, so a box near a display edge is cut where the display ends, like the screen itself.
final class OverlayController {
    private(set) var windows: [OverlayWindow] = []
    private(set) var space = ScreenSpace(displays: [])
    private let store = OverlayStore()
    private var layers: [Int: [String: (OverlayEvent, CALayer)]] = [:]  // per display id
    private var expiryTimer: Timer?
    private let logFrames: Bool
    var onCaption: (String) -> Void = { _ in }
    var onPhantomState: (PhantomState) -> Void = { _ in }

    init(logFrames: Bool) {
        self.logFrames = logFrames
        rebuildWindows()
    }

    func rebuildWindows() {
        for w in windows { w.orderOut(nil) }
        layers = [:]
        let screens = NSScreen.screens  // [0] is the main display (menu bar): global (0, 0) is its top-left
        let displays = screens.map { s -> Display in
            let id = (s.deviceDescription[NSDeviceDescriptionKey("NSScreenNumber")] as? NSNumber)?.intValue ?? 0
            let f = s.frame
            return Display(id: id, appKitFrame: Rect(x: f.minX, y: f.minY, w: f.width, h: f.height), scale: s.backingScaleFactor)
        }
        space = ScreenSpace(displays: displays)
        windows = zip(screens, displays).map { OverlayWindow(screen: $0, display: $1) }
        for w in windows {
            w.orderFrontRegardless()
            print("overlay window: display \(w.display.id) appkit \(Rect(w.frame)) global \(space.globalFrame(w.display)) scale \(w.display.scale)")
        }
        render()
    }

    func setHidden(_ hidden: Bool) {
        for w in windows { if hidden { w.orderOut(nil) } else { w.orderFrontRegardless() } }
    }

    func apply(_ e: OverlayEvent) {
        let change = store.apply(e, now: now())
        switch change {
        case .phantom(let s): onPhantomState(s)
        case .ignored(let why): print("overlay \(e.id): ignored (\(why))")
        case .drew, .removed:
            if e.type == .caption, let t = e.text { onCaption(t) }
            if logFrames || e.type != .clear { log(e) }
            render()
        }
    }

    // ---- rendering
    private func now() -> Double { ProcessInfo.processInfo.systemUptime }

    private func render() {
        let visible = store.visible
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        for w in windows {
            var mine = layers[w.display.id] ?? [:]
            let ids = Set(visible.map(\.id))
            for (id, (_, layer)) in mine where !ids.contains(id) {
                layer.removeFromSuperlayer()
                mine[id] = nil
            }
            for e in visible {
                if let (old, _) = mine[e.id], old == e { continue }
                mine[e.id]?.1.removeFromSuperlayer()
                let layer = makeLayer(e, in: w)
                w.root.addSublayer(layer)
                mine[e.id] = (e, layer)
            }
            layers[w.display.id] = mine
        }
        CATransaction.commit()
        scheduleExpiry()
    }

    private func scheduleExpiry() {
        expiryTimer?.invalidate()
        guard let at = store.nextExpiry else { return }
        expiryTimer = Timer.scheduledTimer(withTimeInterval: max(0.01, at - now()), repeats: false) { [weak self] _ in
            guard let self else { return }
            let gone = store.expire(now: now())
            if logFrames, !gone.isEmpty { print("overlay expired: \(gone.joined(separator: ", "))") }
            render()
        }
    }

    private func log(_ e: OverlayEvent) {
        guard let b = e.bbox else { print("overlay \(e.type.rawValue) \(e.id)"); return }
        let g = Rect(b)
        let d = space.display(for: g)
        let local = d.map { space.local(g, in: $0).description } ?? "off-screen"
        print("overlay \(e.type.rawValue) \(e.id): global \(g) → display \(d?.id ?? -1) view \(local) appkit \(space.appKit(fromGlobal: g))")
    }

    private func makeLayer(_ e: OverlayEvent, in w: OverlayWindow) -> CALayer {
        let container = CALayer()
        container.frame = w.root.bounds
        container.contentsScale = w.display.scale
        // whatever the arrival order: arrows under boxes, captions on top (an arrow never crosses the text)
        container.zPosition = [OverlayKind.arrow: 1, .pulse: 2, .highlight: 3, .caption: 4][e.type] ?? 0
        switch e.type {
        case .highlight:
            if let b = e.bbox { container.addSublayer(highlightLayer(space.local(Rect(b).insetBy(-Layout.highlightPad), in: w.display))) }
        case .pulse:
            if let b = e.bbox { container.addSublayer(pulseLayer(space.local(Rect(b).insetBy(-Layout.highlightPad), in: w.display))) }
        case .arrow:
            if let b = e.bbox, let f = e.fromPoint, let seg = Layout.arrow(from: Pt(f), to: Rect(b)) {
                container.addSublayer(arrowLayer(from: space.local(seg.tail, in: w.display), to: space.local(seg.head, in: w.display)))
            }
        case .caption:
            if let text = e.text { container.addSublayer(captionLayer(text, target: e.bbox.map { Rect($0) }, in: w)) }
        case .clear, .phantomState:
            break
        }
        return container
    }

    private func highlightLayer(_ r: Rect) -> CALayer {
        let l = CAShapeLayer()
        let rect = CGRect(x: r.x, y: r.y, width: r.w, height: r.h)
        l.path = CGPath(roundedRect: rect, cornerWidth: 8, cornerHeight: 8, transform: nil)
        l.fillColor = Style.accent.withAlphaComponent(0.10).cgColor
        l.strokeColor = Style.accent.cgColor
        l.lineWidth = 3
        l.shadowColor = Style.accent.cgColor  // the soft glow
        l.shadowOpacity = 0.9
        l.shadowRadius = 10
        l.shadowOffset = .zero
        let fade = CABasicAnimation(keyPath: "opacity")
        fade.fromValue = 0
        fade.toValue = 1
        fade.duration = 0.18
        l.add(fade, forKey: "in")
        return l
    }

    private func pulseLayer(_ r: Rect) -> CALayer {
        let l = CAShapeLayer()
        l.bounds = CGRect(x: 0, y: 0, width: r.w, height: r.h)
        l.position = CGPoint(x: r.x + r.w / 2, y: r.y + r.h / 2)  // scale about the target's centre
        l.path = CGPath(roundedRect: l.bounds, cornerWidth: 10, cornerHeight: 10, transform: nil)
        l.fillColor = nil
        l.strokeColor = Style.accent.cgColor
        l.lineWidth = 3
        let grow = CABasicAnimation(keyPath: "transform.scale")
        grow.fromValue = 1.0
        grow.toValue = 1.0 + min(0.6, 24 / max(min(r.w, r.h), 1))
        let fade = CABasicAnimation(keyPath: "opacity")
        fade.fromValue = 0.95
        fade.toValue = 0
        let g = CAAnimationGroup()
        g.animations = [grow, fade]
        g.duration = 0.8
        g.repeatCount = .infinity
        g.timingFunction = CAMediaTimingFunction(name: .easeOut)
        l.add(g, forKey: "pulse")
        return l
    }

    private func arrowLayer(from tail: Pt, to head: Pt) -> CALayer {
        let dx = head.x - tail.x, dy = head.y - tail.y
        let len = max((dx * dx + dy * dy).squareRoot(), 1)
        let ux = dx / len, uy = dy / len, size = 16.0
        let base = CGPoint(x: head.x - ux * size, y: head.y - uy * size)
        let path = CGMutablePath()
        path.move(to: CGPoint(x: tail.x, y: tail.y))
        path.addLine(to: base)
        let shaft = CAShapeLayer()
        shaft.path = path
        shaft.strokeColor = Style.accent.cgColor
        shaft.lineWidth = 4
        shaft.lineCap = .round
        shaft.fillColor = nil
        let tip = CGMutablePath()
        tip.move(to: CGPoint(x: head.x, y: head.y))
        tip.addLine(to: CGPoint(x: base.x - uy * size * 0.6, y: base.y + ux * size * 0.6))
        tip.addLine(to: CGPoint(x: base.x + uy * size * 0.6, y: base.y - ux * size * 0.6))
        tip.closeSubpath()
        let head = CAShapeLayer()
        head.path = tip
        head.fillColor = Style.accent.cgColor
        let l = CALayer()
        l.addSublayer(shaft)
        l.addSublayer(head)
        l.shadowColor = NSColor.black.cgColor
        l.shadowOpacity = 0.35
        l.shadowRadius = 3
        l.shadowOffset = CGSize(width: 0, height: -1)
        return l
    }

    private func captionLayer(_ text: String, target: Rect?, in w: OverlayWindow) -> CALayer {
        let attr = NSAttributedString(string: text, attributes: [.font: Style.captionFont, .foregroundColor: NSColor.white])
        let bound = attr.boundingRect(
            with: NSSize(width: Style.captionMaxWidth, height: 10_000), options: [.usesLineFragmentOrigin, .usesFontLeading]
        )
        let size = (w: ceil(bound.width) + 2 * Style.captionPad.x, h: ceil(bound.height) + 2 * Style.captionPad.y)
        // place it on the target's display (or the main one), in global points; then into this window's view
        let home = target.flatMap { space.display(for: $0) } ?? space.displays.first ?? w.display
        let g = Layout.caption(size: size, target: target, display: space.globalFrame(home))
        let r = space.local(g, in: w.display)
        let bg = CALayer()
        bg.frame = CGRect(x: r.x, y: r.y, width: r.w, height: r.h)
        bg.backgroundColor = Style.captionBG.cgColor
        bg.cornerRadius = 10
        bg.borderColor = Style.accent.withAlphaComponent(0.9).cgColor
        bg.borderWidth = 1.5
        bg.shadowColor = NSColor.black.cgColor
        bg.shadowOpacity = 0.3
        bg.shadowRadius = 8
        bg.shadowOffset = CGSize(width: 0, height: -2)
        let t = CATextLayer()
        t.string = attr
        t.isWrapped = true
        t.contentsScale = w.display.scale
        t.frame = CGRect(x: Style.captionPad.x, y: Style.captionPad.y, width: r.w - 2 * Style.captionPad.x, height: r.h - 2 * Style.captionPad.y)
        bg.addSublayer(t)
        return bg
    }
}

extension Rect {
    init(_ r: NSRect) { self.init(x: r.minX, y: r.minY, w: r.width, h: r.height) }
}
