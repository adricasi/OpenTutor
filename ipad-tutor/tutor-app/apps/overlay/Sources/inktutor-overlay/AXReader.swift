import AppKit
import ApplicationServices
import Carbon  // IsSecureEventInputEnabled
import OverlayCore

/// One live accessibility element. Its attributes come in one batched IPC call (AXUIElementCopyMultipleAttributeValues),
/// fetched the first time the walker asks; its value in a second call, only for roles where it means something.
final class LiveAXNode: AXNode {
    let el: AXUIElement
    private var fetched = false
    private var _attrs: AXAttrs?
    private var _kids: [LiveAXNode] = []

    init(_ el: AXUIElement) { self.el = el }

    var attrs: AXAttrs? { fetch(); return _attrs }
    var children: [LiveAXNode] { fetch(); return _kids }
    func same(as other: LiveAXNode) -> Bool { CFEqual(el, other.el) }

    static let names: [String] = [
        kAXRoleAttribute, kAXSubroleAttribute, kAXTitleAttribute, kAXDescriptionAttribute, kAXHelpAttribute,
        kAXPositionAttribute, kAXSizeAttribute, kAXEnabledAttribute, kAXSelectedAttribute, kAXChildrenAttribute,
    ]
    /// Roles whose AXValue is short and useful (a field's text, a checkbox's state, a tab's selection).
    static let valueRoles: Set<String> = [
        "AXTextField", "AXComboBox", "AXCheckBox", "AXRadioButton", "AXSlider", "AXPopUpButton", "AXStaticText",
        "AXCell", "AXValueIndicator", "AXIncrementor", "AXDisclosureTriangle", "AXSearchField", "AXDateField",
        "AXLevelIndicator", "AXMenuButton", "AXTextArea", "AXHeading",
    ]

    private func fetch() {
        guard !fetched else { return }
        fetched = true
        var raw: CFArray?
        guard AXUIElementCopyMultipleAttributeValues(el, Self.names as CFArray, AXCopyMultipleAttributeOptions(rawValue: 0), &raw) == .success,
            let vals = raw as? [AnyObject], vals.count == Self.names.count
        else { return }
        func str(_ i: Int) -> String? { vals[i] as? String }
        func ax(_ i: Int, _ type: AXValueType) -> AXValue? {
            let v = vals[i]
            guard CFGetTypeID(v) == AXValueGetTypeID() else { return nil }
            let a = v as! AXValue
            return AXValueGetType(a) == type ? a : nil
        }
        func point(_ i: Int) -> CGPoint? {
            var p = CGPoint.zero
            guard let a = ax(i, .cgPoint), AXValueGetValue(a, .cgPoint, &p) else { return nil }
            return p
        }
        func size(_ i: Int) -> CGSize? {
            var s = CGSize.zero
            guard let a = ax(i, .cgSize), AXValueGetValue(a, .cgSize, &s) else { return nil }
            return s
        }
        guard let role = str(0) else { return }
        var frame: Rect?
        if let p = point(5), let s = size(6) {
            frame = Rect(x: Double(p.x), y: Double(p.y), w: Double(s.width), h: Double(s.height))
        }
        let enabled = (vals[7] as? Bool) ?? true
        let selected = (vals[8] as? Bool) ?? false
        var a = AXAttrs(role: role, subrole: str(1), title: str(2), desc: str(3), help: str(4), frame: frame, enabled: enabled, selected: selected)
        if !AXWalk.isSecure(a), Self.valueRoles.contains(role) { a.value = valueText(role: role) }
        _attrs = a
        if let kids = vals[9] as? [AXUIElement] { _kids = kids.map(LiveAXNode.init) }
    }

    private func valueText(role: String) -> String? {
        if role == "AXTextArea" {
            var n: CFTypeRef?
            guard AXUIElementCopyAttributeValue(el, kAXNumberOfCharactersAttribute as CFString, &n) == .success,
                let count = n as? Int, count <= 400
            else { return nil }  // a document body: too big, and not what a step is about
        }
        var v: CFTypeRef?
        guard AXUIElementCopyAttributeValue(el, kAXValueAttribute as CFString, &v) == .success, let v else { return nil }
        if let s = v as? String { return s }
        if let n = v as? NSNumber { return n.stringValue }
        return nil
    }

}

/// The frontmost app as the main thread saw it (NSRunningApplication isn't touched off main).
struct FrontApp: Equatable {
    var pid: pid_t
    var name: String
    var bundleId: String
}

/// Reads AX snapshots of the frontmost app on a background queue (AX calls are blocking IPC; a hung app times out
/// after 0.3 s per call instead of freezing the icon).
final class AXReader {
    let queue = DispatchQueue(label: "inktutor.ax", qos: .userInitiated)
    var limits = AXWalk.Limits()

    struct Result {
        var snapshot: AXSnapshot
        var window: Rect?  // the focused window, global points
        var ms: Double
        var visited: Int
        var truncated: Bool
    }

    /// Nothing but app name and an empty element list for a blocklisted app: its contents are never read.
    func blockedSnapshot(_ app: FrontApp, pointer: Point2, display: Int) -> AXSnapshot {
        AXSnapshot(app: app.name, bundleId: app.bundleId, display: display, pointer: pointer, tMs: nowMs())
    }

    func snapshot(_ app: FrontApp, pointer: Point2, space: ScreenSpace, done: @escaping (Result) -> Void) {
        let limits = self.limits
        queue.async {
            let t0 = DispatchTime.now().uptimeNanoseconds
            let appEl = AXUIElementCreateApplication(app.pid)
            AXUIElementSetMessagingTimeout(appEl, 0.3)
            func attr(_ el: AXUIElement, _ name: String) -> CFTypeRef? {
                var v: CFTypeRef?
                return AXUIElementCopyAttributeValue(el, name as CFString, &v) == .success ? v : nil
            }
            func elementAttr(_ el: AXUIElement, _ name: String) -> AXUIElement? {
                guard let v = attr(el, name), CFGetTypeID(v) == AXUIElementGetTypeID() else { return nil }
                return (v as! AXUIElement)
            }
            let windows = (attr(appEl, kAXWindowsAttribute) as? [AXUIElement]) ?? []
            let winEl = elementAttr(appEl, kAXFocusedWindowAttribute) ?? elementAttr(appEl, kAXMainWindowAttribute) ?? windows.first
            let focusEl = elementAttr(appEl, kAXFocusedUIElementAttribute)
            let focused = focusEl.map(LiveAXNode.init)
            var elements: [AXElement] = []
            var focusedId: String?
            var secure = focused?.attrs.map(AXWalk.isSecure) ?? false
            var windowFrame: Rect?
            var windowTitle = ""
            var visited = 0
            var truncated = false

            if let winEl {
                let win = LiveAXNode(winEl)
                windowFrame = win.attrs?.frame
                windowTitle = win.attrs.flatMap(AXWalk.title) ?? ""
                let r = AXWalk.walk(win, prefix: "w", focused: focused, visible: windowFrame, limits: limits)
                elements = r.elements
                focusedId = r.focusedId
                secure = secure || r.secureFocused
                visited += r.visited
                truncated = r.truncated
            }
            // the app's other windows (dialogs, palettes): role, title and frame only
            for (i, w) in windows.enumerated() where winEl.map({ !CFEqual($0, w) }) ?? true {
                let n = LiveAXNode(w)
                visited += 1
                if let a = n.attrs, let f = a.frame, f.w > 0, f.h > 0 {
                    elements.append(AXElement(id: "win\(i)", role: a.role, title: AXWalk.title(a), frame: f.bbox, enabled: a.enabled, path: [AXWalk.pathPart(a)]))
                }
            }
            // an open menu: the menu bar's items and the open menu's contents
            if let bar = elementAttr(appEl, kAXMenuBarAttribute) {
                let items = LiveAXNode(bar).children
                if let open = items.first(where: { $0.attrs?.selected == true }) {
                    var menuLimits = limits
                    menuLimits.maxElements = 150
                    menuLimits.maxDepth = 6
                    for (i, it) in items.enumerated() where !it.same(as: open) {
                        if let a = it.attrs, let f = a.frame, f.w > 0 {
                            elements.append(AXElement(id: "m\(i)", role: a.role, title: AXWalk.title(a), frame: f.bbox, enabled: a.enabled, path: ["AXMenuBar:", AXWalk.pathPart(a)]))
                        }
                    }
                    let idx = items.firstIndex(where: { $0.same(as: open) }) ?? 0
                    let r = AXWalk.walk(open, prefix: "m\(idx)", basePath: ["AXMenuBar:"], focused: focused, limits: menuLimits)
                    elements += r.elements
                    if focusedId == nil { focusedId = r.focusedId }
                    visited += r.visited
                }
            }
            // focus the walk didn't reach (too deep): send it on its own, with its ancestors' path
            if focusedId == nil, let focused, let fa = focused.attrs {
                var chain: [AXAttrs] = []
                var cur = focusEl.flatMap { elementAttr($0, kAXParentAttribute) }
                while let c = cur, chain.count < 20 {
                    let n = LiveAXNode(c)
                    guard let a = n.attrs, a.role != "AXApplication" else { break }
                    chain.insert(a, at: 0)
                    cur = elementAttr(c, kAXParentAttribute)
                }
                if let e = AXWalk.focusedElement(fa, ancestors: chain) {
                    elements.append(e)
                    focusedId = e.id
                }
            }
            let secureInput = IsSecureEventInputEnabled()
            let shown = AXWalk.redact(elements, secureFocused: secure, secureInput: secureInput)
            let display = windowFrame.flatMap { space.display(for: $0)?.id } ?? space.displays.first?.id ?? 0
            let snap = AXSnapshot(
                app: app.name, bundleId: app.bundleId, windowTitle: secure || secureInput ? "" : windowTitle, display: display,
                elements: shown, focusedId: shown.isEmpty ? nil : focusedId, secureFieldFocused: secure || secureInput,
                pointer: pointer, tMs: nowMs()
            )
            let ms = Double(DispatchTime.now().uptimeNanoseconds - t0) / 1e6
            let result = Result(snapshot: snap, window: windowFrame, ms: ms, visited: visited, truncated: truncated)
            DispatchQueue.main.async { done(result) }
        }
    }
}

/// Wall-clock milliseconds (epoch): every `t_ms` the helper sends.
func nowMs() -> Double { (Date().timeIntervalSince1970 * 1000).rounded() }
