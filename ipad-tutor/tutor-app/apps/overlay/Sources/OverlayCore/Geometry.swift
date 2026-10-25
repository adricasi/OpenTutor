import Foundation

/// A rectangle in some 2-D space; which one (global top-left points, AppKit, a window's view) is up to the caller.
public struct Rect: Equatable, Sendable, CustomStringConvertible {
    public var x: Double
    public var y: Double
    public var w: Double
    public var h: Double

    public init(x: Double, y: Double, w: Double, h: Double) { self.x = x; self.y = y; self.w = w; self.h = h }
    public init(_ b: BBox) { self.init(x: b.x, y: b.y, w: b.w, h: b.h) }

    public var minX: Double { x }
    public var minY: Double { y }
    public var maxX: Double { x + w }
    public var maxY: Double { y + h }
    public var mid: Pt { Pt(x: x + w / 2, y: y + h / 2) }
    public var area: Double { max(0, w) * max(0, h) }
    public var bbox: BBox { BBox(x: x, y: y, w: w, h: h) }

    public func insetBy(_ d: Double) -> Rect { Rect(x: x + d, y: y + d, w: w - 2 * d, h: h - 2 * d) }

    public func contains(_ p: Pt) -> Bool { p.x >= minX && p.x <= maxX && p.y >= minY && p.y <= maxY }

    public func intersection(_ o: Rect) -> Rect? {
        let x0 = max(minX, o.minX), y0 = max(minY, o.minY), x1 = min(maxX, o.maxX), y1 = min(maxY, o.maxY)
        return x1 > x0 && y1 > y0 ? Rect(x: x0, y: y0, w: x1 - x0, h: y1 - y0) : nil
    }

    public var description: String { "(\(fmt(x)), \(fmt(y)), \(fmt(w))×\(fmt(h)))" }
}

public struct Pt: Equatable, Sendable {
    public var x: Double
    public var y: Double

    public init(x: Double, y: Double) { self.x = x; self.y = y }
    public init(_ p: Point2) { self.init(x: p.x, y: p.y) }
}

func fmt(_ v: Double) -> String { v == v.rounded() ? String(Int(v)) : String(format: "%.1f", v) }

/// One display as AppKit reports it (`NSScreen.frame`: global, bottom-left origin of the main display, y up).
public struct Display: Equatable, Sendable {
    public var id: Int
    public var appKitFrame: Rect
    public var scale: Double

    public init(id: Int, appKitFrame: Rect, scale: Double) { self.id = id; self.appKitFrame = appKitFrame; self.scale = scale }
}

/// Converts between the protocol's global points (origin top-left of the main display, y down: the AX / Quartz
/// display space) and AppKit's global coordinates (origin bottom-left of the main display, y up), and into one
/// display window's view coordinates. The main display is the one with the menu bar: `NSScreen.screens[0]`.
public struct ScreenSpace: Equatable, Sendable {
    public var displays: [Display]  // displays[0] is the main display

    public init(displays: [Display]) { self.displays = displays }

    /// Height of the main display: the line both y axes are mirrored about.
    public var mainHeight: Double { displays.first?.appKitFrame.h ?? 0 }

    public func appKit(fromGlobal r: Rect) -> Rect { Rect(x: r.x, y: mainHeight - r.y - r.h, w: r.w, h: r.h) }
    public func global(fromAppKit r: Rect) -> Rect { Rect(x: r.x, y: mainHeight - r.y - r.h, w: r.w, h: r.h) }
    public func appKit(fromGlobal p: Pt) -> Pt { Pt(x: p.x, y: mainHeight - p.y) }

    /// A global rect in the view of the window that covers `display` (the view's origin is the display's
    /// bottom-left corner, y up; the window's frame is the display's frame).
    public func local(_ r: Rect, in display: Display) -> Rect {
        let a = appKit(fromGlobal: r)
        return Rect(x: a.x - display.appKitFrame.x, y: a.y - display.appKitFrame.y, w: a.w, h: a.h)
    }

    public func local(_ p: Pt, in display: Display) -> Pt {
        let a = appKit(fromGlobal: p)
        return Pt(x: a.x - display.appKitFrame.x, y: a.y - display.appKitFrame.y)
    }

    /// A display's bounds in global points.
    public func globalFrame(_ d: Display) -> Rect { global(fromAppKit: d.appKitFrame) }

    /// The display a global rect belongs to: the one holding its centre, else the one it overlaps most.
    public func display(for r: Rect) -> Display? {
        if let d = displays.first(where: { globalFrame($0).contains(r.mid) }) { return d }
        let best = displays.map { ($0, globalFrame($0).intersection(r)?.area ?? 0) }.max { $0.1 < $1.1 }
        guard let best, best.1 > 0 else { return nil }
        return best.0
    }

    /// What the helper reports in `screen.hello`.
    public func displayInfos() -> [DisplayInfo] {
        displays.enumerated().map { i, d in
            DisplayInfo(id: d.id, frame: globalFrame(d).bbox, scale: d.scale, main: i == 0)
        }
    }
}
