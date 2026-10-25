import Foundation

/// What is on screen right now, keyed by event id, with TTLs. The AppKit side renders `visible` after every change.
///
/// - an event with an id already drawn replaces it (and restarts its TTL);
/// - `clear`: with `refers_to`, removes that event; else with `step_id`, everything of that step; with neither,
///   everything; pulses on a cleared event go with it;
/// - `pulse` with `refers_to` and no bbox pulses that event's box (dropped if it isn't drawn);
/// - `phantom_state` changes the phantom icon; it is not drawn as an overlay.
public final class OverlayStore {
    public static let defaultPulseMs = 2400

    public struct Item: Equatable {
        public var event: OverlayEvent
        public var expiresAt: Double?  // seconds, same clock as `now`
    }

    public enum Change: Equatable {
        case drew(String)
        case removed([String])
        case phantom(PhantomState)
        case ignored(String)  // why
    }

    public private(set) var items: [String: Item] = [:]
    private var order: [String] = []
    public private(set) var phantom: PhantomState = .idle

    public init() {}

    /// Drawn events in arrival order.
    public var visible: [OverlayEvent] { order.compactMap { items[$0]?.event } }

    @discardableResult
    public func apply(_ e: OverlayEvent, now: Double) -> Change {
        switch e.type {
        case .phantomState:
            guard let s = e.state else { return .ignored("phantom_state without state") }
            phantom = s
            return .phantom(s)
        case .clear:
            var hit: Set<String>
            if let ref = e.refersTo {
                hit = [ref]
            } else if let step = e.stepId {
                hit = Set(order.filter { items[$0]?.event.stepId == step })
            } else {
                hit = Set(order)
            }
            // and whatever points at a cleared event (a pulse on that highlight)
            hit.formUnion(order.filter { id in items[id]?.event.refersTo.map(hit.contains) ?? false })
            let gone = order.filter(hit.contains)
            remove(gone)
            return .removed(gone)
        case .pulse:
            var p = e
            if p.bbox == nil {
                guard let ref = p.refersTo, let target = items[ref]?.event.bbox else {
                    return .ignored("pulse without a bbox or a drawn target")
                }
                p.bbox = target
            }
            if p.ttlMs == nil { p.ttlMs = Self.defaultPulseMs }
            put(p, now: now)
            return .drew(p.id)
        case .highlight, .arrow:
            guard e.bbox != nil else { return .ignored("\(e.type.rawValue) without bbox") }
            if e.type == .arrow && e.fromPoint == nil { return .ignored("arrow without from_point") }
            put(e, now: now)
            return .drew(e.id)
        case .caption:
            guard let t = e.text, !t.isEmpty else { return .ignored("caption without text") }
            put(e, now: now)
            return .drew(e.id)
        }
    }

    /// Removes what has expired by `now`; returns the ids removed.
    @discardableResult
    public func expire(now: Double) -> [String] {
        let gone = order.filter { (items[$0]?.expiresAt ?? .infinity) <= now }
        remove(gone)
        return gone
    }

    /// When the next TTL runs out, if any.
    public var nextExpiry: Double? { items.values.compactMap(\.expiresAt).min() }

    private func put(_ e: OverlayEvent, now: Double) {
        if items[e.id] == nil { order.append(e.id) }
        items[e.id] = Item(event: e, expiresAt: e.ttlMs.map { now + Double($0) / 1000 })
    }

    private func remove(_ ids: [String]) {
        let set = Set(ids)
        order.removeAll { set.contains($0) }
        for id in ids { items[id] = nil }
    }
}
