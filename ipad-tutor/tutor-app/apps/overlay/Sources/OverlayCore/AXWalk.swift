import Foundation

/// What the AX reader needs of one accessibility element. The live reader fills it from AXUIElement (one batched
/// IPC call per element); overlay-selftest fills it from a fake tree.
public struct AXAttrs: Equatable, Sendable {
    public var role: String
    public var subrole: String?
    public var title: String?  // AXTitle
    public var desc: String?  // AXDescription
    public var help: String?  // AXHelp
    public var value: String?  // AXValue as text; the walker drops it for secure fields
    public var frame: Rect?  // AXPosition + AXSize: global points, top-left origin (the protocol's space already)
    public var enabled: Bool
    public var selected: Bool

    public init(
        role: String, subrole: String? = nil, title: String? = nil, desc: String? = nil, help: String? = nil,
        value: String? = nil, frame: Rect? = nil, enabled: Bool = true, selected: Bool = false
    ) {
        self.role = role; self.subrole = subrole; self.title = title; self.desc = desc; self.help = help
        self.value = value; self.frame = frame; self.enabled = enabled; self.selected = selected
    }
}

/// One node of an accessibility tree.
public protocol AXNode {
    var attrs: AXAttrs? { get }  // nil: the element is gone or doesn't answer
    var children: [Self] { get }
    func same(as other: Self) -> Bool
}

public enum AXWalk {
    public static let titleMax = 80
    public static let valueMax = 200

    /// AXTitle, else AXDescription, else AXHelp; trimmed, never empty.
    public static func title(_ a: AXAttrs) -> String? {
        for s in [a.title, a.desc, a.help] {
            if let t = s?.trimmingCharacters(in: .whitespacesAndNewlines), !t.isEmpty { return String(t.prefix(titleMax)) }
        }
        return nil
    }

    /// Password fields: NSSecureTextField is AXTextField with subrole AXSecureTextField (some apps use the role).
    public static func isSecure(_ a: AXAttrs) -> Bool {
        a.role == "AXSecureTextField" || a.subrole == "AXSecureTextField"
    }

    /// "AXRadioButton:Insert", "AXTabGroup:" (empty title kept, so paths line up with the tree).
    public static func pathPart(_ a: AXAttrs) -> String { "\(a.role):\(title(a) ?? "")" }

    /// Roles worth sending even without a title or value (the user can act on them).
    public static let actionable: Set<String> = [
        "AXButton", "AXMenuButton", "AXPopUpButton", "AXCheckBox", "AXRadioButton", "AXTextField", "AXTextArea",
        "AXComboBox", "AXSlider", "AXIncrementor", "AXDisclosureTriangle", "AXLink", "AXMenuItem", "AXMenuBarItem",
        "AXTab", "AXTabGroup", "AXSearchField", "AXSecureTextField", "AXColorWell", "AXDateField", "AXSheet",
        "AXDialog", "AXWindow", "AXToolbar", "AXCell", "AXRow", "AXOutline", "AXTable",
    ]

    /// Containers whose children aren't worth walking past a few (spreadsheet grids, long lists).
    public static let bulky: Set<String> = ["AXTable", "AXOutline", "AXList", "AXGrid", "AXBrowser", "AXRow", "AXColumn"]

    public struct Limits: Equatable, Sendable {
        public var maxElements = 300
        public var maxDepth = 14
        public var maxChildren = 80  // per node
        public var maxBulkyChildren = 24  // per table / list / row
        public var maxVisited = 2000  // AX calls are IPC: stop walking eventually even if little was sendable

        public init() {}
    }

    public struct Result: Equatable, Sendable {
        public var elements: [AXElement] = []
        public var focusedId: String?
        public var secureFocused = false
        public var visited = 0
        public var truncated = false
    }

    /// The value as sent: never for secure fields, trimmed and capped.
    public static func value(_ a: AXAttrs) -> String? {
        guard !isSecure(a), let v = a.value?.trimmingCharacters(in: .whitespacesAndNewlines), !v.isEmpty else { return nil }
        return String(v.prefix(valueMax))
    }

    /// Worth sending: has a title or a value, or is something the user can act on.
    public static func sendable(_ a: AXAttrs) -> Bool {
        title(a) != nil || value(a) != nil || actionable.contains(a.role) || isSecure(a)
    }

    /// Breadth-first from `root` (the focused window, or an open menu), shallow elements first so the toolbar /
    /// ribbon is in before a deep grid fills the budget. Only elements whose frame overlaps `visible` (if given)
    /// are sent; an off-screen element's subtree is skipped. Ids are child-index chains from the root
    /// (`prefix`, "w.0.3"), stable while the window's structure is.
    public static func walk<N: AXNode>(
        _ root: N, prefix: String = "w", basePath: [String] = [], focused: N? = nil, visible: Rect? = nil,
        limits: Limits = Limits()
    ) -> Result {
        var r = Result()
        var queue: [(node: N, depth: Int, id: String, path: [String])] = [(root, 0, prefix, basePath)]
        var head = 0
        while head < queue.count {
            let (node, depth, id, parentPath) = queue[head]
            head += 1
            if r.visited >= limits.maxVisited || r.elements.count >= limits.maxElements {
                r.truncated = true
                break
            }
            r.visited += 1
            guard let a = node.attrs else { continue }
            if let f = a.frame, let v = visible, f.w > 0, f.h > 0, f.intersection(v) == nil { continue }  // off-screen
            let path = parentPath + [pathPart(a)]
            let isFocused = focused.map { node.same(as: $0) } ?? false
            if isFocused {
                r.focusedId = id
                if isSecure(a) { r.secureFocused = true }
            }
            if let f = a.frame, f.w > 0, f.h > 0, sendable(a) || isFocused {
                r.elements.append(AXElement(id: id, role: a.role, title: title(a), value: value(a), frame: f.bbox, enabled: a.enabled, path: path))
            }
            guard depth < limits.maxDepth else { continue }
            let kids = node.children
            let cap = bulky.contains(a.role) ? limits.maxBulkyChildren : limits.maxChildren
            for (i, k) in kids.prefix(cap).enumerated() {
                queue.append((k, depth + 1, "\(id).\(i)", path))
            }
            if kids.count > cap { r.truncated = true }
        }
        return r
    }

    /// What a snapshot may carry: nothing at all while a password field has focus (by AX, or because macOS has
    /// secure keyboard entry on), else the walk's elements.
    public static func redact(_ elements: [AXElement], secureFocused: Bool, secureInput: Bool) -> [AXElement] {
        secureFocused || secureInput ? [] : elements
    }

    /// The focused element when the walk didn't reach it (too deep, over budget): sent on its own so the server
    /// still knows what has focus. Its path is its ancestors' parts, outermost first, if the reader could get them.
    public static func focusedElement(_ a: AXAttrs, ancestors: [AXAttrs]) -> AXElement? {
        guard let f = a.frame, f.w > 0, f.h > 0 else { return nil }
        return AXElement(
            id: "focus", role: a.role, title: title(a), value: value(a), frame: f.bbox, enabled: a.enabled,
            path: ancestors.map(pathPart) + [pathPart(a)]
        )
    }
}

/// App blocklist (`screen.config`): matched by app name (case-insensitive, also "1Password 7" for "1Password")
/// or bundle id.
public func isBlocked(app: String, bundleId: String, blocklist: [String]) -> Bool {
    let name = app.lowercased(), bid = bundleId.lowercased()
    for raw in blocklist {
        let b = raw.trimmingCharacters(in: .whitespaces).lowercased()
        guard !b.isEmpty else { continue }
        if name == b || bid == b || name.hasPrefix(b + " ") { return true }
    }
    return false
}

/// Frame downscale maths: a display of `points` (w × h) at `backingScale` pixels per point becomes a JPEG at most
/// `maxWidth` pixels wide (never upscaled); `scale` is JPEG pixels per point (what `ScreenFrame.scale` carries).
public enum FrameMath {
    public static func size(points w: Double, _ h: Double, backingScale: Double, maxWidth: Int = 1280) -> (width: Int, height: Int, scale: Double) {
        guard w > 0, h > 0 else { return (0, 0, 0) }
        let native = w * max(backingScale, 0.1)
        let pxW = min(Double(maxWidth), native.rounded())
        let scale = pxW / w
        return (Int(pxW), Int((h * scale).rounded()), scale)
    }

    /// A JPEG pixel → global points: `origin + px / scale` (how the server maps VLM boxes back).
    public static func toGlobal(px x: Double, _ y: Double, origin: Point2, scale: Double) -> Point2 {
        Point2(x: origin.x + x / scale, y: origin.y + y / scale)
    }
}
