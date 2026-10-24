import Foundation

/// Where things go, in global points (top-left origin, y down). Pure, so overlay-selftest can check it.
public enum Layout {
    public static let highlightPad = 4.0  // the box sits this far outside the control
    public static let captionGap = 10.0  // between the target and its caption
    public static let edge = 8.0  // keep captions this far from the display's edges

    /// A caption of `size` (w, h) next to `target`: below it, else above, else to its right, else left; clamped into
    /// `display`. Without a target: bottom centre of the display.
    public static func caption(size: (w: Double, h: Double), target: Rect?, display: Rect) -> Rect {
        let w = min(size.w, display.w - 2 * edge), h = min(size.h, display.h - 2 * edge)
        func clampX(_ x: Double) -> Double { min(max(x, display.minX + edge), display.maxX - edge - w) }
        func clampY(_ y: Double) -> Double { min(max(y, display.minY + edge), display.maxY - edge - h) }
        guard let t = target?.insetBy(-highlightPad) else {
            return Rect(x: clampX(display.mid.x - w / 2), y: clampY(display.maxY - display.h * 0.12 - h), w: w, h: h)
        }
        let cx = clampX(t.mid.x - w / 2)
        if t.maxY + captionGap + h <= display.maxY - edge { return Rect(x: cx, y: t.maxY + captionGap, w: w, h: h) }
        if t.minY - captionGap - h >= display.minY + edge { return Rect(x: cx, y: t.minY - captionGap - h, w: w, h: h) }
        let cy = clampY(t.mid.y - h / 2)
        if t.maxX + captionGap + w <= display.maxX - edge { return Rect(x: t.maxX + captionGap, y: cy, w: w, h: h) }
        return Rect(x: clampX(t.minX - captionGap - w), y: cy, w: w, h: h)
    }

    /// An arrow from `tail` to the edge of `target` (stopping `gap` short of the highlight), or nil if the tail is
    /// already inside it.
    public static func arrow(from tail: Pt, to target: Rect, gap: Double = 6) -> (tail: Pt, head: Pt)? {
        let box = target.insetBy(-highlightPad)
        if box.contains(tail) { return nil }
        let c = box.mid, dx = c.x - tail.x, dy = c.y - tail.y
        let len = (dx * dx + dy * dy).squareRoot()
        guard len > 0 else { return nil }
        // distance from the centre back towards the tail at which the ray leaves the box
        let tx = dx == 0 ? Double.infinity : (box.w / 2) / abs(dx)
        let ty = dy == 0 ? Double.infinity : (box.h / 2) / abs(dy)
        let s = min(tx, ty)  // fraction of (dx, dy) from the centre to the box edge
        let edgeDist = s * len
        let headDist = max(0, len - edgeDist - gap)
        return (tail, Pt(x: tail.x + dx / len * headDist, y: tail.y + dy / len * headDist))
    }
}
