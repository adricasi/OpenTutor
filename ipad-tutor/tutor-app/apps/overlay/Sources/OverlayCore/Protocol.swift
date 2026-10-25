import Foundation

// Mirrors packages/protocol (inktutor_protocol/models.py → schema.json). JSON keys are the protocol's field names,
// spelled out in each CodingKeys. apps/server/tests/test_overlay_contract.py checks every `// protocol: <Name>`
// struct and `// protocol-enum: <Model>.<field>` enum here against schema.json, so the two sides can't drift.
// All boxes and points are global display points: origin top-left of the main display, y down.

// protocol: BBox
public struct BBox: Codable, Equatable, Sendable {
    public var x: Double
    public var y: Double
    public var w: Double
    public var h: Double

    public init(x: Double, y: Double, w: Double, h: Double) {
        self.x = x; self.y = y; self.w = w; self.h = h
    }

    enum CodingKeys: String, CodingKey { case x, y, w, h }
}

// protocol: Point2
public struct Point2: Codable, Equatable, Sendable {
    public var x: Double
    public var y: Double

    public init(x: Double, y: Double) { self.x = x; self.y = y }

    enum CodingKeys: String, CodingKey { case x, y }
}

// protocol-enum: OverlayEvent.type
public enum OverlayKind: String, Codable, Sendable {
    case highlight, arrow, caption, pulse, clear
    case phantomState = "phantom_state"
}

// protocol-enum: OverlayEvent.state
public enum PhantomState: String, Codable, Sendable {
    case idle, listening, thinking, speaking, paused
    case capturePaused = "capture_paused"
}

// protocol: OverlayEvent
public struct OverlayEvent: Codable, Equatable, Sendable {
    public var id: String
    public var type: OverlayKind
    public var bbox: BBox?
    public var fromPoint: Point2?
    public var text: String?
    public var stepId: String?
    public var ttlMs: Int?
    public var refersTo: String?
    public var state: PhantomState?

    public init(
        id: String, type: OverlayKind, bbox: BBox? = nil, fromPoint: Point2? = nil, text: String? = nil,
        stepId: String? = nil, ttlMs: Int? = nil, refersTo: String? = nil, state: PhantomState? = nil
    ) {
        self.id = id; self.type = type; self.bbox = bbox; self.fromPoint = fromPoint; self.text = text
        self.stepId = stepId; self.ttlMs = ttlMs; self.refersTo = refersTo; self.state = state
    }

    enum CodingKeys: String, CodingKey {
        case id, type, bbox, text, state
        case fromPoint = "from_point"
        case stepId = "step_id"
        case ttlMs = "ttl_ms"
        case refersTo = "refers_to"
    }
}

// protocol: DisplayInfo
public struct DisplayInfo: Codable, Equatable, Sendable {
    public var id: Int
    public var frame: BBox
    public var scale: Double
    public var main: Bool

    public init(id: Int, frame: BBox, scale: Double, main: Bool) {
        self.id = id; self.frame = frame; self.scale = scale; self.main = main
    }

    enum CodingKeys: String, CodingKey { case id, frame, scale, main }
}

// ---------------------------------------------------------------- helper → server

// protocol: ScreenHello
public struct ScreenHello: Codable, Equatable, Sendable {
    public var type = "screen.hello"
    public var helper: String
    public var displays: [DisplayInfo]

    public init(helper: String, displays: [DisplayInfo]) { self.helper = helper; self.displays = displays }

    enum CodingKeys: String, CodingKey { case type, helper, displays }
}

// protocol: GuideGoal
public struct GuideGoal: Codable, Equatable, Sendable {
    public var type = "guide.goal"
    public var text: String

    public init(text: String) { self.text = text }

    enum CodingKeys: String, CodingKey { case type, text }
}

// protocol-enum: GuideControl.action
public enum GuideAction: String, Codable, Sendable {
    case next, back, `repeat`, pause, resume, stop
}

// protocol: GuideControl
public struct GuideControl: Codable, Equatable, Sendable {
    public var type = "guide.control"
    public var action: GuideAction

    public init(action: GuideAction) { self.action = action }

    enum CodingKeys: String, CodingKey { case type, action }
}

/// Only the field the phantom icon sets (listen on/off); the protocol's other fields stay absent.
// protocol: SessionUpdate
public struct SessionUpdate: Codable, Equatable, Sendable {
    public var type = "session.update"
    public var voice: Bool?

    public init(voice: Bool?) { self.voice = voice }

    enum CodingKeys: String, CodingKey { case type, voice }
}

// protocol: AXElement
public struct AXElement: Codable, Equatable, Sendable {
    public var id: String
    public var role: String
    public var title: String?
    public var value: String?  // never for secure text fields
    public var frame: BBox
    public var enabled: Bool
    public var path: [String]

    public init(id: String, role: String, title: String? = nil, value: String? = nil, frame: BBox, enabled: Bool = true, path: [String] = []) {
        self.id = id; self.role = role; self.title = title; self.value = value; self.frame = frame
        self.enabled = enabled; self.path = path
    }

    enum CodingKeys: String, CodingKey { case id, role, title, value, frame, enabled, path }
}

/// The frontmost app's visible elements; empty (and `secure_field_focused`) while a password field has focus.
// protocol: AXSnapshot
public struct AXSnapshot: Codable, Equatable, Sendable {
    public var type = "ax.snapshot"
    public var app: String
    public var bundleId: String
    public var windowTitle: String
    public var display: Int
    public var elements: [AXElement]
    public var focusedId: String?
    public var secureFieldFocused: Bool
    public var pointer: Point2?
    public var tMs: Double

    public init(
        app: String, bundleId: String = "", windowTitle: String = "", display: Int = 0, elements: [AXElement] = [],
        focusedId: String? = nil, secureFieldFocused: Bool = false, pointer: Point2? = nil, tMs: Double
    ) {
        self.app = app; self.bundleId = bundleId; self.windowTitle = windowTitle; self.display = display
        self.elements = elements; self.focusedId = focusedId; self.secureFieldFocused = secureFieldFocused
        self.pointer = pointer; self.tMs = tMs
    }

    enum CodingKeys: String, CodingKey {
        case type, app, display, elements, pointer
        case bundleId = "bundle_id"
        case windowTitle = "window_title"
        case focusedId = "focused_id"
        case secureFieldFocused = "secure_field_focused"
        case tMs = "t_ms"
    }
}

// protocol-enum: ScreenFrame.trigger
public enum FrameTrigger: String, Codable, Sendable {
    case tick, click, focus
}

// protocol: ScreenFrame
public struct ScreenFrame: Codable, Equatable, Sendable {
    public var type = "screen.frame"
    public var id: String
    public var display: Int
    public var origin: Point2  // the display's top-left, global points
    public var width: Int  // JPEG pixels
    public var height: Int
    public var scale: Double  // JPEG pixels per point
    public var jpegB64: String
    public var window: BBox?  // the frontmost window, global points
    public var tMs: Double
    public var trigger: FrameTrigger

    public init(
        id: String, display: Int, origin: Point2, width: Int, height: Int, scale: Double, jpegB64: String,
        window: BBox?, tMs: Double, trigger: FrameTrigger
    ) {
        self.id = id; self.display = display; self.origin = origin; self.width = width; self.height = height
        self.scale = scale; self.jpegB64 = jpegB64; self.window = window; self.tMs = tMs; self.trigger = trigger
    }

    enum CodingKeys: String, CodingKey {
        case type, id, display, origin, width, height, scale, window, trigger
        case jpegB64 = "jpeg_b64"
        case tMs = "t_ms"
    }
}

// protocol-enum: InputEvent.kind
public enum InputKind: String, Codable, Sendable {
    case click, key
}

/// That the user clicked (and where) or typed. Keys carry no content, not even key codes.
// protocol: InputEvent
public struct InputEvent: Codable, Equatable, Sendable {
    public var type = "input.event"
    public var kind: InputKind
    public var tMs: Double
    public var at: Point2?

    public init(kind: InputKind, tMs: Double, at: Point2? = nil) { self.kind = kind; self.tMs = tMs; self.at = at }

    enum CodingKeys: String, CodingKey {
        case type, kind, at
        case tMs = "t_ms"
    }
}

/// The microphone: ~100 ms of 16 kHz mono PCM16, base64. `pen` (canvas only) is left out, i.e. null.
// protocol: VoiceAudio
public struct VoiceAudio: Codable, Equatable, Sendable {
    public var type = "voice.audio"
    public var pcm16B64: String
    public var sampleRate: Int

    public init(pcm16B64: String, sampleRate: Int = 16000) { self.pcm16B64 = pcm16B64; self.sampleRate = sampleRate }

    enum CodingKeys: String, CodingKey {
        case type
        case pcm16B64 = "pcm16_b64"
        case sampleRate = "sample_rate"
    }
}

/// Barge-in: the user started talking over the tutor (playback already stopped here).
// protocol: VoiceStop
public struct VoiceStop: Codable, Equatable, Sendable {
    public var type = "voice.stop"

    public init() {}

    enum CodingKeys: String, CodingKey { case type }
}

// ---------------------------------------------------------------- server → helper

// protocol: OverlayMsg
public struct OverlayMsg: Codable, Equatable, Sendable {
    public var type = "overlay"
    public var event: OverlayEvent
    public var provider: String
    public var model: String

    enum CodingKeys: String, CodingKey { case type, event, provider, model }
}

// protocol: ScreenConfigMsg
public struct ScreenConfigMsg: Codable, Equatable, Sendable {
    public var type = "screen.config"
    public var blocklist: [String]
    public var captureFps: Double
    public var allowAutomation: Bool  // informational: this helper has no code that clicks, types or scrolls

    enum CodingKeys: String, CodingKey {
        case type, blocklist
        case captureFps = "capture_fps"
        case allowAutomation = "allow_automation"
    }
}

// protocol: ErrorMsg
public struct ErrorMsg: Codable, Equatable, Sendable {
    public var type = "error"
    public var message: String

    enum CodingKeys: String, CodingKey { case type, message }
}

// protocol: VoiceTranscript
public struct VoiceTranscript: Codable, Equatable, Sendable {
    public var type = "voice.transcript"
    public var text: String
    public var isFinal: Bool

    enum CodingKeys: String, CodingKey {
        case type, text
        case isFinal = "final"
    }
}

/// The tutor's voice: PCM16 mono at `sample_rate` (24 kHz from Kokoro).
// protocol: VoiceAudioOut
public struct VoiceAudioOut: Codable, Equatable, Sendable {
    public var type = "voice.audio"
    public var pcm16B64: String
    public var sampleRate: Int
    public var feedbackId: String?

    enum CodingKeys: String, CodingKey {
        case type
        case pcm16B64 = "pcm16_b64"
        case sampleRate = "sample_rate"
        case feedbackId = "feedback_id"
    }
}

// protocol-enum: RuntimeStatus.level
public enum MemoryLevel: String, Codable, Sendable {
    case ok, low, critical
}

// protocol: RuntimeStatus
public struct RuntimeStatus: Codable, Equatable, Sendable {
    public var availableGb: Double
    public var level: MemoryLevel
    public var tier2Model: String
    public var tier2Mode: String  // full, offload, refused, external, loading, unavailable, off
    public var detail: String
    public var event: String
    public var eventId: Int

    enum CodingKeys: String, CodingKey {
        case level, detail, event
        case availableGb = "available_gb"
        case tier2Model = "tier2_model"
        case tier2Mode = "tier2_mode"
        case eventId = "event_id"
    }
}

// protocol: RuntimeMsg
public struct RuntimeMsg: Codable, Equatable, Sendable {
    public var type = "runtime"
    public var status: RuntimeStatus

    enum CodingKeys: String, CodingKey { case type, status }
}

// protocol: Target
public struct Target: Codable, Equatable, Sendable {
    public var axPath: [String]?
    public var titleRegex: String?
    public var role: String?
    public var description: String?
    public var bbox: BBox?
    public var source: String?  // "ax" | "vlm", nil until located

    enum CodingKeys: String, CodingKey {
        case role, description, bbox, source
        case axPath = "ax_path"
        case titleRegex = "title_regex"
    }
}

// protocol: DoneWhen
public struct DoneWhen: Codable, Equatable, Sendable {
    public var kind: String  // ax_exists, ax_value, window_title, vlm_yes_no, user_next
    public var args: [String: String]

    enum CodingKeys: String, CodingKey { case kind, args }
}

// protocol: GuideStep
public struct GuideStep: Codable, Equatable, Sendable {
    public var id: String
    public var n: Int
    public var instruction: String
    public var speak: String
    public var target: Target?
    public var doneWhen: DoneWhen
    public var hintLevels: [String]

    enum CodingKeys: String, CodingKey {
        case id, n, instruction, speak, target
        case doneWhen = "done_when"
        case hintLevels = "hint_levels"
    }
}

// protocol: GuidePlan
public struct GuidePlan: Codable, Equatable, Sendable {
    public var goal: String
    public var app: String
    public var steps: [GuideStep]
    public var version: Int

    enum CodingKeys: String, CodingKey { case goal, app, steps, version }
}

// protocol: GuidePlanMsg
public struct GuidePlanMsg: Codable, Equatable, Sendable {
    public var type = "guide.plan"
    public var plan: GuidePlan

    enum CodingKeys: String, CodingKey { case type, plan }
}

// protocol-enum: GuideStatusMsg.phase
public enum GuidePhase: String, Codable, Sendable {
    case idle, planning, locating, guiding, verifying, replanning, paused, done, error
}

// protocol: GuideStatusMsg
public struct GuideStatusMsg: Codable, Equatable, Sendable {
    public var type = "guide.status"
    public var phase: GuidePhase
    public var step: Int
    public var of: Int
    public var detail: String

    public init(phase: GuidePhase, step: Int = 0, of: Int = 0, detail: String = "") {
        self.phase = phase; self.step = step; self.of = of; self.detail = detail
    }

    enum CodingKeys: String, CodingKey { case type, phase, step, of, detail }
}

/// `ScreenServerMessage`, decoded by its `type`. Types this helper doesn't know are kept as `.unknown`.
public enum ServerMessage: Equatable, Sendable {
    case overlay(OverlayMsg)
    case config(ScreenConfigMsg)
    case plan(GuidePlanMsg)
    case status(GuideStatusMsg)
    case transcript(VoiceTranscript)
    case audio(VoiceAudioOut)
    case runtime(RuntimeMsg)
    case error(ErrorMsg)
    case unknown(String)

    private struct Head: Decodable { var type: String }

    public static func decode(_ data: Data) throws -> ServerMessage {
        let d = JSONDecoder()
        switch try d.decode(Head.self, from: data).type {
        case "overlay": return .overlay(try d.decode(OverlayMsg.self, from: data))
        case "screen.config": return .config(try d.decode(ScreenConfigMsg.self, from: data))
        case "guide.plan": return .plan(try d.decode(GuidePlanMsg.self, from: data))
        case "guide.status": return .status(try d.decode(GuideStatusMsg.self, from: data))
        case "voice.transcript": return .transcript(try d.decode(VoiceTranscript.self, from: data))
        case "voice.audio": return .audio(try d.decode(VoiceAudioOut.self, from: data))
        case "runtime": return .runtime(try d.decode(RuntimeMsg.self, from: data))
        case "error": return .error(try d.decode(ErrorMsg.self, from: data))
        case let other: return .unknown(other)
        }
    }
}

public enum Wire {
    /// JSON for a helper → server message. Nil optionals are left out (the server's models forbid unknown keys).
    public static func encode<T: Encodable>(_ msg: T) throws -> String {
        let e = JSONEncoder()
        e.outputFormatting = [.sortedKeys, .withoutEscapingSlashes]
        return String(decoding: try e.encode(msg), as: UTF8.self)
    }
}
