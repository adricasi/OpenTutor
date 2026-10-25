// Pure-logic self-check for the overlay helper (the Command Line Tools have no XCTest, so no `swift test`).
//   swift run overlay-selftest [path/to/screen-messages.json]      exits 1 on any failure
import Foundation
import OverlayCore

var failures = 0
var checks = 0

func check(_ ok: Bool, _ what: String, file: StaticString = #fileID, line: UInt = #line) {
    checks += 1
    if !ok {
        failures += 1
        print("FAIL \(what)  (\(file):\(line))")
    }
}

func near(_ a: Double, _ b: Double, _ eps: Double = 1e-6) -> Bool { abs(a - b) <= eps }
func near(_ a: Rect, _ b: Rect) -> Bool { near(a.x, b.x) && near(a.y, b.y) && near(a.w, b.w) && near(a.h, b.h) }
func near(_ a: Pt, _ b: Pt) -> Bool { near(a.x, b.x) && near(a.y, b.y) }

// ---------------------------------------------------------------- coordinates
// One display: a 1512×982 MacBook screen. Global (top-left, y down) ↔ AppKit (bottom-left, y up).
do {
    let main = Display(id: 1, appKitFrame: Rect(x: 0, y: 0, w: 1512, h: 982), scale: 2)
    let space = ScreenSpace(displays: [main])
    let insert = Rect(x: 318, y: 58, w: 52, h: 24)  // a ribbon tab near the top
    check(near(space.appKit(fromGlobal: insert), Rect(x: 318, y: 982 - 58 - 24, w: 52, h: 24)), "global → AppKit flips y")
    check(near(space.global(fromAppKit: space.appKit(fromGlobal: insert)), insert), "AppKit → global round-trips")
    check(near(space.local(insert, in: main), Rect(x: 318, y: 900, w: 52, h: 24)), "main display view = AppKit")
    check(near(space.appKit(fromGlobal: Pt(x: 10, y: 0)), Pt(x: 10, y: 982)), "global top edge is AppKit maxY")
    check(near(space.globalFrame(main), Rect(x: 0, y: 0, w: 1512, h: 982)), "main display's global frame")
    let info = space.displayInfos()
    check(info.count == 1 && info[0].main && info[0].scale == 2 && info[0].id == 1, "hello describes the display")
}

// Two displays (logic only: the reference Mac has one): a 1920×1080 external to the left, its top 98 pt above the main one's.
// AppKit: x = -1920, y = 982 - 1080 + 98 = 0 … so its bottom-left is (-1920, 0) and it spans y 0…1080.
do {
    let main = Display(id: 1, appKitFrame: Rect(x: 0, y: 0, w: 1512, h: 982), scale: 2)
    let ext = Display(id: 2, appKitFrame: Rect(x: -1920, y: 0, w: 1920, h: 1080), scale: 1)
    let space = ScreenSpace(displays: [main, ext])
    check(near(space.globalFrame(ext), Rect(x: -1920, y: -98, w: 1920, h: 1080)), "external display's global frame")
    let onExt = Rect(x: -1800, y: -50, w: 100, h: 40)  // near the external display's top-left
    check(space.display(for: onExt)?.id == 2, "box on the external display found there")
    check(space.display(for: Rect(x: 300, y: 50, w: 50, h: 20))?.id == 1, "box on the main display found there")
    check(space.display(for: Rect(x: 5000, y: 5000, w: 10, h: 10)) == nil, "off-screen box has no display")
    // in the external window's view: y up from its bottom-left; top of the display is y = 1080
    check(near(space.local(onExt, in: ext), Rect(x: 120, y: 1080 - 48 - 40, w: 100, h: 40)), "local on external display")
    // a display above the main one (y < 0 globally, AppKit y > mainHeight)
    let above = Display(id: 3, appKitFrame: Rect(x: 0, y: 982, w: 1512, h: 982), scale: 2)
    let s3 = ScreenSpace(displays: [main, above])
    check(near(s3.globalFrame(above), Rect(x: 0, y: -982, w: 1512, h: 982)), "display above has negative global y")
    check(near(s3.local(Rect(x: 10, y: -972, w: 20, h: 20), in: above), Rect(x: 10, y: 952, w: 20, h: 20)), "local on display above")
    let infos = space.displayInfos()
    check(infos.map(\.main) == [true, false] && infos[1].frame == BBox(x: -1920, y: -98, w: 1920, h: 1080), "hello lists both")
}

// ---------------------------------------------------------------- layout
do {
    let display = Rect(x: 0, y: 0, w: 1512, h: 982)
    let target = Rect(x: 318, y: 58, w: 52, h: 24)
    let c = Layout.caption(size: (w: 200, h: 36), target: target, display: display)
    check(near(c.y, target.maxY + Layout.highlightPad + Layout.captionGap), "caption goes below the target")
    check(near(c.mid.x, target.mid.x), "caption centred under the target")
    let low = Rect(x: 700, y: 940, w: 60, h: 24)  // near the bottom: no room below
    let c2 = Layout.caption(size: (w: 200, h: 36), target: low, display: display)
    check(c2.maxY <= low.minY - Layout.highlightPad, "caption flips above when there's no room below")
    let left = Rect(x: 2, y: 100, w: 30, h: 20)  // at the left edge: clamped inside
    let c3 = Layout.caption(size: (w: 300, h: 36), target: left, display: display)
    check(c3.minX >= display.minX + Layout.edge, "caption clamped into the display")
    let free = Layout.caption(size: (w: 300, h: 40), target: nil, display: display)
    check(near(free.mid.x, display.mid.x) && free.maxY < display.maxY && free.minY > display.mid.y, "untargeted caption sits bottom centre")
    let ext = Rect(x: -1920, y: -98, w: 1920, h: 1080)
    let c4 = Layout.caption(size: (w: 200, h: 36), target: Rect(x: -1000, y: 400, w: 50, h: 20), display: ext)
    check(c4.minX >= ext.minX && c4.maxX <= ext.maxX, "caption on a display with negative origin")

    if let a = Layout.arrow(from: Pt(x: 900, y: 600), to: target) {
        let box = target.insetBy(-Layout.highlightPad)
        check(!box.contains(a.head), "arrow head stops outside the highlight")
        let dx = a.head.x - box.mid.x, dy = a.head.y - box.mid.y
        check((dx * dx + dy * dy).squareRoot() < 60, "arrow head lands at the highlight's edge")
        // the head lies on the line from the tail to the target centre
        let cross = (a.head.x - 900) * (box.mid.y - 600) - (a.head.y - 600) * (box.mid.x - 900)
        check(abs(cross) < 1, "arrow points at the target's centre")
    } else {
        check(false, "arrow from far away exists")
    }
    check(Layout.arrow(from: target.mid, to: target) == nil, "no arrow from inside the target")
    if let a = Layout.arrow(from: Pt(x: 344, y: 400), to: target) {  // straight below
        check(near(a.head.x, 344) && near(a.head.y, target.maxY + Layout.highlightPad + 6), "vertical arrow head")
    } else {
        check(false, "vertical arrow exists")
    }
}

// ---------------------------------------------------------------- overlay state
do {
    let s = OverlayStore()
    let box = BBox(x: 318, y: 58, w: 52, h: 24)
    check(s.apply(OverlayEvent(id: "h1", type: .highlight, bbox: box, stepId: "s1", ttlMs: 1000), now: 10) == .drew("h1"), "highlight drawn")
    s.apply(OverlayEvent(id: "c1", type: .caption, bbox: box, text: "Click Insert", stepId: "s1"), now: 10)
    s.apply(OverlayEvent(id: "x1", type: .highlight, bbox: box, stepId: "s2"), now: 10)
    check(s.visible.map(\.id) == ["h1", "c1", "x1"], "arrival order")
    check(s.nextExpiry.map { near($0, 11) } == true, "TTL becomes an expiry time")
    check(s.expire(now: 10.5).isEmpty, "nothing expires early")
    s.apply(OverlayEvent(id: "h1", type: .highlight, bbox: box, stepId: "s1", ttlMs: 1000), now: 10.8)  // replaced: TTL restarts
    check(s.visible.map(\.id) == ["h1", "c1", "x1"], "same id replaces in place")
    check(s.expire(now: 11.2).isEmpty, "replacing restarts the TTL")
    check(s.expire(now: 11.8) == ["h1"], "TTL removes the event")
    check(s.apply(OverlayEvent(id: "p", type: .pulse, refersTo: "gone"), now: 12) == .ignored("pulse without a bbox or a drawn target"), "pulse on nothing ignored")
    s.apply(OverlayEvent(id: "p1", type: .pulse, refersTo: "x1"), now: 12)
    check(s.items["p1"]?.event.bbox == box && s.items["p1"]?.event.ttlMs == OverlayStore.defaultPulseMs, "pulse takes its target's box and a default TTL")
    check(s.apply(OverlayEvent(id: "k", type: .clear, refersTo: "x1"), now: 12) == .removed(["x1", "p1"]), "clear by id takes its pulses too")
    s.apply(OverlayEvent(id: "a", type: .highlight, bbox: box, stepId: "s3"), now: 12)
    s.apply(OverlayEvent(id: "p2", type: .pulse, refersTo: "c1"), now: 12)
    check(s.apply(OverlayEvent(id: "k", type: .clear, stepId: "s1"), now: 12) == .removed(["c1", "p2"]), "clear by step takes pulses on it")
    check(s.apply(OverlayEvent(id: "k", type: .clear), now: 12) == .removed(["a"]) && s.visible.isEmpty, "clear all")
    check(s.apply(OverlayEvent(id: "ph", type: .phantomState, state: .thinking), now: 12) == .phantom(.thinking) && s.phantom == .thinking, "phantom state")
    check(s.visible.isEmpty, "phantom state isn't drawn")
    check(s.apply(OverlayEvent(id: "z", type: .highlight), now: 12) == .ignored("highlight without bbox"), "highlight needs a bbox")
    check(s.apply(OverlayEvent(id: "z", type: .arrow, bbox: box), now: 12) == .ignored("arrow without from_point"), "arrow needs from_point")
    check(s.apply(OverlayEvent(id: "z", type: .caption, text: ""), now: 12) == .ignored("caption without text"), "caption needs text")
}

// ---------------------------------------------------------------- options and backoff
do {
    func url(_ args: [String]) -> String? { (try? Options.parse(args).url())?.absoluteString }
    check(url([]) == "ws://127.0.0.1:8000/ws/screen/mac", "default server")
    check(url(["--server", "8033"]) == "ws://127.0.0.1:8033/ws/screen/mac", "--server PORT")
    check(url(["--server", "localhost:8033", "--session", "s-1"]) == "ws://localhost:8033/ws/screen/s-1", "--server HOST:PORT")
    check(url(["--server=ws://127.0.0.1:9000"]) == "ws://127.0.0.1:9000/ws/screen/mac", "--server=ws://…")
    check(url(["--server", "http://127.0.0.1:8033/"]) == "ws://127.0.0.1:8033/ws/screen/mac", "http → ws")
    check(url(["--server", "192.168.1.20:8000"]) == nil, "LAN servers refused")
    check(url(["--server", "abc.trycloudflare.com"]) == nil, "tunnel refused")
    check(url(["--server", "wss://127.0.0.1:8000"]) == nil, "wss refused")
    check(url(["--session", "../x"]) == nil, "bad session id refused")
    check(url(["--bogus"]) == nil, "unknown flag refused")
    check((try? Options.parse(["--log-frames"]))?.logFrames == true, "--log-frames")

    var b = Backoff()
    let ds = (0..<8).map { _ in b.next(jitter: 0.5) }
    check(zip(ds, [0.5, 1, 2, 4, 8, 10, 10, 10]).allSatisfy { near($0, $1) }, "backoff doubles up to 10 s")
    b.reset()
    check(near(b.next(jitter: 0), 0.4) && near(b.next(jitter: 1), 1.2), "jitter spreads ±20 %")
}

// ---------------------------------------------------------------- wire examples shared with the server
do {
    let path = CommandLine.arguments.dropFirst().first
        ?? URL(fileURLWithPath: #filePath).deletingLastPathComponent().appendingPathComponent("../../Fixtures/screen-messages.json").standardized.path
    guard let data = FileManager.default.contents(atPath: path),
        let root = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
        let server = root["server"] as? [[String: Any]], let client = root["client"] as? [[String: Any]]
    else {
        print("FAIL cannot read wire examples at \(path)")
        exit(1)
    }
    var kinds = Set<String>()
    for (i, msg) in server.enumerated() {
        let raw = try! JSONSerialization.data(withJSONObject: msg)
        do {
            switch try ServerMessage.decode(raw) {
            case .overlay(let m): kinds.insert(m.event.type.rawValue)
            case .config(let c): check(c.blocklist.contains("1Password") && !c.allowAutomation, "screen.config decoded")
            case .error(let e): check(!e.message.isEmpty, "error decoded")
            case .plan(let p):
                kinds.insert("guide.plan")
                check(p.plan.steps.count == 3 && p.plan.steps[0].target?.axPath?.last == "AXRadioButton:Insertar", "guide.plan decoded")
                check(p.plan.steps[0].doneWhen.kind == "ax_exists" && p.plan.steps[0].doneWhen.args["role"] == "AXButton", "done_when decoded")
                check(p.plan.steps[1].target?.source == "ax" && p.plan.steps[2].target == nil && p.plan.steps[0].hintLevels.count == 2, "plan targets decoded")
            case .status(let st): kinds.insert("guide.status:\(st.phase.rawValue)")
            case .transcript(let t): kinds.insert("voice.transcript:\(t.isFinal)")
            case .audio(let a):
                kinds.insert("voice.audio")
                check(a.sampleRate == 24000 && a.feedbackId == "cap-insert" && PCM16.decode(Data(base64Encoded: a.pcm16B64)!).count == 6, "voice.audio decoded")
            case .runtime(let r):
                kinds.insert("runtime")
                check(r.status.level == .low && r.status.eventId == 3 && near(r.status.availableGb, 4.2), "runtime decoded")
            case .unknown(let t): check(false, "server example \(i) has unknown type \(t)")
            }
        } catch {
            check(false, "server example \(i) (\(msg["type"] ?? "?")) decodes: \(error)")
        }
    }
    check(kinds.isSuperset(of: ["highlight", "arrow", "caption", "pulse", "clear", "phantom_state"]), "examples cover every overlay type")
    check(kinds.isSuperset(of: ["guide.plan", "guide.status:planning", "guide.status:guiding", "guide.status:error", "guide.status:done",
                                "voice.transcript:true", "voice.transcript:false", "voice.audio", "runtime"]), "examples cover every new server message")
    if case .overlay(let m)? = try? ServerMessage.decode(try! JSONSerialization.data(withJSONObject: server[1])) {
        check(m.event.fromPoint == Point2(x: 900, y: 600) && m.provider == "local", "snake_case from_point decoded")
    }
    if case .overlay(let m)? = try? ServerMessage.decode(try! JSONSerialization.data(withJSONObject: server[0])) {
        check(m.event.ttlMs == 8000 && m.event.stepId == "s1", "ttl_ms and step_id decoded")
    }
    check((try? ServerMessage.decode(Data(#"{"type":"future.thing","x":1}"#.utf8))) == .unknown("future.thing"), "unknown types tolerated")

    // what the helper sends must be byte-for-byte the shapes the server accepts
    let mine: [String] = [
        try! Wire.encode(ScreenHello(helper: helperName, displays: [
            DisplayInfo(id: 1, frame: BBox(x: 0, y: 0, w: 1512, h: 982), scale: 2, main: true),
            DisplayInfo(id: 2, frame: BBox(x: -1920, y: -98, w: 1920, h: 1080), scale: 1, main: false),
        ])),
        try! Wire.encode(GuideGoal(text: "make a pivot table of sales by region")),
        try! Wire.encode(GuideControl(action: .pause)),
        try! Wire.encode(GuideControl(action: .stop)),
        try! Wire.encode(SessionUpdate(voice: true)),
        try! Wire.encode(SessionUpdate(voice: false)),
        try! Wire.encode(AXSnapshot(
            app: "Microsoft Excel", bundleId: "com.microsoft.Excel", windowTitle: "Libro1", display: 1,
            elements: [
                AXElement(id: "w", role: "AXWindow", title: "Libro1", frame: BBox(x: 0, y: 25, w: 1512, h: 920), path: ["AXWindow:Libro1"]),
                AXElement(id: "w.0.2", role: "AXRadioButton", title: "Insertar", value: "0", frame: BBox(x: 318, y: 58, w: 52, h: 24),
                          path: ["AXWindow:Libro1", "AXTabGroup:", "AXRadioButton:Insertar"]),
                AXElement(id: "w.1", role: "AXTextField", frame: BBox(x: 100, y: 140, w: 300, h: 22), enabled: false, path: ["AXWindow:Libro1", "AXTextField:"]),
            ],
            focusedId: "w.0.2", pointer: Point2(x: 900, y: 600), tMs: 1761300000123.5)),
        try! Wire.encode(AXSnapshot(app: "Safari", bundleId: "com.apple.Safari", display: 1, secureFieldFocused: true, tMs: 1761300001000)),
        try! Wire.encode(ScreenFrame(
            id: "f12", display: 1, origin: Point2(x: 0, y: 0), width: 1280, height: 831, scale: 1280.0 / 1512.0,
            jpegB64: "/9j/4AAQSkZJRgABAQ==", window: BBox(x: 0, y: 25, w: 1512, h: 920), tMs: 1761300000200, trigger: .click)),
        try! Wire.encode(ScreenFrame(
            id: "f13", display: 1, origin: Point2(x: 0, y: 0), width: 1280, height: 831, scale: 1280.0 / 1512.0,
            jpegB64: "/9j/4AAQSkZJRgABAQ==", window: nil, tMs: 1761300001200, trigger: .tick)),
        try! Wire.encode(InputEvent(kind: .click, tMs: 1761300000100, at: Point2(x: 344, y: 70))),
        try! Wire.encode(InputEvent(kind: .key, tMs: 1761300000500)),
        try! Wire.encode(VoiceAudio(pcm16B64: PCM16.base64([0, 0.5, 1, -1, -0.5]))),
        try! Wire.encode(VoiceStop()),
    ]
    check(mine.count == client.count, "one encoded message per client example")
    for (i, (enc, want)) in zip(mine, client).enumerated() {
        let got = try! JSONSerialization.jsonObject(with: Data(enc.utf8)) as! NSDictionary
        check(got.isEqual(to: want), "client example \(i) (\(want["type"] ?? "?")): helper sends \(enc)")
    }
    check(!(try! Wire.encode(SessionUpdate(voice: nil))).contains("null"), "nil fields are left out, not null")
}

// ---------------------------------------------------------------- voice: resampler, PCM16, chunks, level
do {
    func sine(_ n: Int, rate: Double, hz: Double = 440, amp: Float = 0.5, phase0: Int = 0) -> [Float] {
        (0..<n).map { amp * Float(sin(2 * Double.pi * hz * Double($0 + phase0) / rate)) }
    }
    // 3 s of a 48 kHz sine in 100 ms buffers → 16 kHz mono, cut into 100 ms chunks (the converter holds back a
    // few ms of filter delay and releases its output in bursts, so totals are checked over the whole run)
    let fmt48 = Resampler.buffer([[0]], rate: 48000)!.format
    let rs = Resampler(input: fmt48)!
    var pipe = MicPipeline()
    var total = 0
    var chunks: [[Float]] = []
    for k in 0..<30 {
        let x = rs.convert(Resampler.buffer([sine(4800, rate: 48000, phase0: k * 4800)], rate: 48000)!)
        total += x.count
        chunks += pipe.push(x).chunks
    }
    check(total <= 48000 && 48000 - total <= 320, "48 kHz → 16 kHz: 3 s in, 48000 samples out less ≤ 20 ms of filter delay (got \(total))")
    check(chunks.count == total / 1600 && chunks.allSatisfy { $0.count == 1600 }, "100 ms chunks of exactly 1600 samples (got \(chunks.count))")
    let mid = chunks[chunks.count / 2]
    check(abs(rms(mid) - 0.5 / Float(2).squareRoot()) < 0.02, "resampled sine keeps its level (rms \(rms(mid)))")
    // the 440 Hz tone is still 440 Hz: count zero crossings in one 100 ms chunk (≈ 88)
    let zc = zip(mid, mid.dropFirst()).filter { ($0 < 0) != ($1 < 0) }.count
    check(abs(zc - 88) <= 3, "resampled sine keeps its pitch (\(zc) zero crossings per 100 ms)")
    check(PCM16.encode(mid).count == 3200, "a 100 ms chunk is 3200 bytes of PCM16")
    // stereo 44.1 kHz → mono 16 kHz (downmixed, not just the left channel)
    let st = Resampler(input: Resampler.buffer([[0], [0]], rate: 44100)!.format)!
    let l = sine(4410, rate: 44100, amp: 0.4), r = [Float](repeating: 0, count: 4410)
    var stOut: [Float] = []
    for _ in 0..<30 { stOut += st.convert(Resampler.buffer([l, r], rate: 44100)!) }
    check(stOut.count <= 48000 && 48000 - stOut.count <= 320, "44.1 kHz stereo → 16 kHz (got \(stOut.count))")
    check(rms(stOut.suffix(1600)) > 0.05, "stereo is mixed down to mono")

    check(PCM16.base64([0, 0.5, 1, -1, -0.5]) == "AAAAQP9/AYAAwA==", "PCM16 little-endian, rounded, clamped")
    check(PCM16.encode([2, -3, .nan]) == PCM16.encode([1, -1, 0]), "PCM16 clamps and drops NaN")
    let back = PCM16.decode(PCM16.encode(mid))
    check(back.count == mid.count && zip(back, mid).allSatisfy { abs($0 - $1) < 1.0 / 16000 }, "PCM16 round-trips")
    check(levelFraction(0) == 0 && near(levelFraction(0.001), 0) && near(levelFraction(0.316227766), 1, 1e-4), "level: −60 dB silent, −10 dB full")
    check(near(levelFraction(0.0316227766), 0.6, 1e-4), "level is log scale")
}

// ---------------------------------------------------------------- barge-in
do {
    func frame(_ amp: Float) -> [Float] { (0..<320).map { amp * Float(sin(Double($0) * 0.3)) } }
    var b = BargeIn()
    var fired = 0
    for _ in 0..<30 { if b.frame(frame(0.004)) { fired += 1 } }
    check(fired == 0, "room noise doesn't barge in")
    let loud = frame(0.2)
    check(!b.frame(loud) && !b.frame(loud), "two loud frames aren't speech yet")
    check(b.frame(loud), "the third loud frame (60 ms) is speech")
    check(!b.frame(loud), "fires once per utterance")
    _ = b.frame(frame(0.004))
    check(!b.frame(loud) && !b.frame(loud) && b.frame(loud), "fires again after a pause")
    var echo = BargeIn()
    echo.minRms = 0.06  // tutor talking, no echo cancellation
    var e = 0
    for _ in 0..<10 { if echo.frame(frame(0.05)) { e += 1 } }
    check(e == 0, "the tutor's own voice through the speakers (below minRms) doesn't barge in")
    // the speakers' echo, learned during playback's first moments, raises the bar for barge-in
    var learn = BargeIn()
    learn.minRms = 0.05
    for _ in 0..<12 { learn.learn(frame(0.08)) }  // ~250 ms of the tutor's voice through the speakers
    var falseHits = 0
    for _ in 0..<20 { if learn.frame(frame(0.08)) { falseHits += 1 } }
    check(learn.floor > 0.05 && falseHits == 0, "echo learned: the tutor's own voice doesn't barge in (floor \(learn.floor))")
    check(!learn.frame(frame(0.4)) && !learn.frame(frame(0.4)) && learn.frame(frame(0.4)), "a voice well above the echo still barges in")
    var lp = MicPipeline()
    lp.learning = true
    check(!lp.push(sine1600(amp: 0.3)).speechStarted && lp.barge.floor > 0.1, "pipeline in learning mode never fires")
    var p = MicPipeline()
    var started = false
    _ = p.push([Float](repeating: 0.001, count: 3200))
    let out = p.push(sine1600(amp: 0.3))
    started = out.speechStarted
    check(started && out.chunks.count == 1 && out.levels.count == 1 && out.levels[0] > 0.15, "pipeline: speech start + chunk level")
}

func sine1600(amp: Float) -> [Float] { (0..<1600).map { amp * Float(sin(Double($0) * 0.17)) } }

// ---------------------------------------------------------------- status model: icon + bubble
do {
    var s = StatusModel(server: "127.0.0.1:8000")
    check(s.icon(now: 0) == .disconnected, "starts disconnected")
    s.starting(now: 0)
    check(s.visibleBubble(now: 1)?.text == "Connecting to the app server (127.0.0.1:8000)…", "connecting…")
    s.tick(now: 2.1)
    check(s.visibleBubble(now: 2.1)?.text == "Not connected to the app server (127.0.0.1:8000)", "then: not connected")
    s.askSent(now: 3)
    check(s.visibleBubble(now: 3)?.text.contains("make ipad") == true && s.icon(now: 3) == .disconnected, "ask while disconnected says how to start it")
    s.setConnected(true, now: 10)
    check(s.icon(now: 10) == .idle && s.visibleBubble(now: 10)?.text == "Connected to the tutor", "connected → idle")
    s.tick(now: 13)
    check(s.visibleBubble(now: 13) == nil, "the connected note hides by itself")

    s.askSent(now: 20)
    check(s.icon(now: 20) == .thinking && s.visibleBubble(now: 20)?.text == StatusModel.sentText, "ask → thinking at once, 'Sent — waiting…'")
    s.tick(now: 24.9)
    check(s.visibleBubble(now: 24.9)?.text == StatusModel.sentText, "still waiting before 5 s")
    s.tick(now: 25.0)
    check(s.visibleBubble(now: 25)?.text == StatusModel.noAnswerText && s.visibleBubble(now: 25)?.kind == .error, "5 s without a reply → 'No answer from the tutor yet'")
    check(s.icon(now: 25) == .thinking, "keeps spinning while it may still answer")
    s.tick(now: 20 + StatusModel.giveUp)
    check(s.icon(now: 20 + StatusModel.giveUp) == .idle, "gives up spinning eventually")

    s.askSent(now: 100)
    s.guideStatus(GuideStatusMsg(phase: .planning), now: 101)
    check(s.icon(now: 101) == .thinking && s.visibleBubble(now: 101)?.text == "Got it — planning…", "planning → thinking, 'Got it — planning…'")
    s.tick(now: 107)
    check(s.visibleBubble(now: 107)?.text == "Got it — planning…", "a reply cancels the timeout message")
    s.guideStatus(GuideStatusMsg(phase: .guiding, step: 1, of: 0, detail: "Haz clic en Insertar"), now: 107.5)
    check(s.visibleBubble(now: 107.5)?.text == "Step 1: Haz clic en Insertar" && s.guiding,
          "step 1 before the whole plan arrived: 'Step 1: …' (no 'of 0'), walkthrough controls on")
    s.heard("haz una tabla dinámica", final: true, now: 107.6)
    s.guideStatus(GuideStatusMsg(phase: .planning), now: 107.7)
    check(s.visibleYouSaid(now: 107.7)?.text == "You said: “haz una tabla dinámica”" && s.visibleBubble(now: 107.7)?.text == "Got it — planning…",
          "what you said stays in its own bubble while the status bubble moves on")
    check(s.visibleYouSaid(now: 107.6 + StatusModel.saidTtl) == nil, "'You said' hides after a while")
    s.planArrived(steps: 5, now: 108)
    s.guideStatus(GuideStatusMsg(phase: .guiding, step: 2, of: 5, detail: "Click Insertar"), now: 109)
    check(s.visibleBubble(now: 109)?.text == "Step 2 of 5: Click Insertar" && s.visibleBubble(now: 200) != nil, "guiding → 'Step 2 of 5: …', stays up")
    check(s.icon(now: 109) == .idle && s.guiding, "guiding: icon idle, walkthrough on")
    s.setListening(true, now: 110)
    check(s.icon(now: 110) == .listening, "listen → listening")
    s.guideStatus(GuideStatusMsg(phase: .verifying, step: 2, of: 5, detail: "Waiting for you to open the dialog…"), now: 111)
    check(s.visibleBubble(now: 111)?.text == "Waiting for you to open the dialog…", "verifying shows the detail")
    s.guideStatus(GuideStatusMsg(phase: .guiding, step: 3, of: 5, detail: "Step 3 of 5: Pick a range"), now: 112)
    check(s.visibleBubble(now: 112)?.text == "Step 3 of 5: Pick a range", "no doubled 'Step n of N'")
    s.setSpeaking(true, now: 113)
    check(s.icon(now: 113) == .speaking, "audio playing → speaking")
    s.setSpeaking(false, now: 114)
    check(s.icon(now: 114) == .listening, "audio done → listening")
    s.setCaptureBlock("Paused: 1Password is on the blocklist — nothing is captured", now: 115)
    check(s.icon(now: 115) == .capturePaused && s.visibleBubble(now: 115)?.text.hasPrefix("Paused: 1Password") == true, "blocklisted app → capture paused + reason")
    s.setCaptureBlock(nil, now: 116)
    check(s.icon(now: 116) == .listening, "capture resumes")
    s.heard("how do I make a", final: false, now: 117)
    check(s.visibleBubble(now: 117)?.text == "“how do I make a…”" && s.visibleBubble(now: 117)?.kind == .heard, "partial transcript shows live")
    s.heard("How do I make a chart?", final: true, now: 118)
    check(s.visibleBubble(now: 118)?.text == "You: How do I make a chart?" && s.icon(now: 118) == .thinking, "final transcript → thinking")
    s.tick(now: 123.5)
    check(s.visibleBubble(now: 123.5)?.text == StatusModel.noAnswerText, "an unanswered question times out too")
    s.guideStatus(GuideStatusMsg(phase: .done, step: 5, of: 5, detail: "Done!"), now: 124)
    check(s.visibleBubble(now: 124)?.text == "Done!" && !s.guiding, "done → 'Done!', walkthrough over")
    s.tick(now: 131)
    check(s.visibleBubble(now: 131) == nil, "'Done!' hides by itself")
    s.guideStatus(GuideStatusMsg(phase: .error, detail: "The tutor model isn't loaded yet — start the app server with `make ipad`"), now: 140)
    check(s.icon(now: 140) == .error && s.visibleBubble(now: 140)?.kind == .error && s.visibleBubble(now: 140)?.text.contains("make ipad") == true, "error phase → error icon + message")
    s.tick(now: 151)
    check(s.icon(now: 151) == .listening, "the error icon clears; the message lingers a bit")
    s.micProblem("Microphone access is off", now: 160)
    check(s.icon(now: 160) == .error && !s.listening && s.visibleBubble(now: 160)?.text == "Microphone access is off", "mic denied → said, listening off")
    s.tick(now: 164)
    check(s.icon(now: 164) == .idle, "then idle")
    s.setPaused(true, now: 170)
    check(s.icon(now: 170) == .paused, "pause → paused")
    s.setPaused(false, now: 171)
    s.serverPhantom(.thinking)
    check(s.icon(now: 171) == .thinking, "server phantom_state thinking")
    s.serverPhantom(.idle)
    s.guideStatus(GuideStatusMsg(phase: .planning), now: 171.5)
    s.serverError("bad message: Field required", now: 172)
    check(s.icon(now: 172) == .error && s.visibleBubble(now: 172)?.text == "bad message: Field required", "server error shown")
    s.tick(now: 177)
    check(s.icon(now: 177) == .idle, "an error message ends 'planning': no endless spinner")
    s.setConnected(false, now: 180)
    check(s.icon(now: 180) == .disconnected && s.visibleBubble(now: 999)?.text == s.notConnectedText, "drop → disconnected, message stays")
    check(Set(IconState.allCases.map { $0.rawValue }).count == 8, "eight icon states")
}

// ---------------------------------------------------------------- transcript log
do {
    var l = TranscriptLog()
    l.add(.you, "make a pivot table", t: 1)
    l.add(.you, "how do", t: 2, partial: true)
    check(l.add(.you, "how do I", t: 3, partial: true), "partial replaced in place")
    l.add(.you, "How do I sort?", t: 4)
    check(l.lines.map(\.text) == ["make a pivot table", "How do I sort?"] && !l.lines[1].partial, "final replaces the partial")
    l.add(.status, "Got it — planning…", t: 5)
    l.add(.status, "Got it — planning…", t: 6)
    check(l.lines.count == 3, "a repeated status line isn't logged twice")
    l.add(.step, "1. Click Insert", t: 7)
    l.add(.tutor, "  ", t: 8)
    check(l.lines.count == 4 && l.lines.last?.who == .step, "empty lines dropped")
    check(TranscriptLog.clock(0).count == 8, "times as HH:mm:ss")
}

// ---------------------------------------------------------------- AX walk: paths, titles, secure fields, budget
final class FakeAX: AXNode {
    let a: AXAttrs?
    let kids: [FakeAX]
    init(_ a: AXAttrs?, _ kids: [FakeAX] = []) { self.a = a; self.kids = kids }
    var attrs: AXAttrs? { a }
    var children: [FakeAX] { kids }
    func same(as other: FakeAX) -> Bool { self === other }
}

do {
    func R(_ x: Double, _ y: Double, _ w: Double, _ h: Double) -> Rect { Rect(x: x, y: y, w: w, h: h) }
    let insert = FakeAX(AXAttrs(role: "AXRadioButton", title: "Insertar", value: "1", frame: R(318, 58, 52, 24)))
    let home = FakeAX(AXAttrs(role: "AXRadioButton", title: "Inicio", value: "0", frame: R(260, 58, 50, 24)))
    let tabs = FakeAX(AXAttrs(role: "AXTabGroup", frame: R(250, 55, 400, 30)), [home, insert])
    let ribbon = FakeAX(AXAttrs(role: "AXGroup", frame: R(0, 25, 1512, 120)), [tabs])
    let password = FakeAX(AXAttrs(role: "AXTextField", subrole: "AXSecureTextField", title: "Password", value: "hunter2", frame: R(100, 300, 200, 22)))
    let close = FakeAX(AXAttrs(role: "AXButton", desc: "Close", frame: R(8, 30, 14, 14)))
    let sort = FakeAX(AXAttrs(role: "AXButton", help: "Sort ascending", frame: R(700, 60, 30, 30), enabled: false))
    let hiddenChild = FakeAX(AXAttrs(role: "AXButton", title: "Hidden child", frame: R(3010, 3010, 20, 20)))
    let offscreen = FakeAX(AXAttrs(role: "AXButton", title: "Offscreen", frame: R(3000, 3000, 50, 20)), [hiddenChild])
    let label = FakeAX(AXAttrs(role: "AXStaticText", value: "  Total  ", frame: R(400, 400, 60, 18)))
    let frameless = FakeAX(AXAttrs(role: "AXGroup"), [label])
    let gone = FakeAX(nil)
    let win = FakeAX(AXAttrs(role: "AXWindow", title: "Libro1", frame: R(0, 25, 1512, 920)), [ribbon, password, close, sort, offscreen, frameless, gone])
    let visible = R(0, 25, 1512, 920)

    let r = AXWalk.walk(win, focused: insert, visible: visible)
    let byTitle = Dictionary(r.elements.compactMap { e in e.title.map { ($0, e) } }, uniquingKeysWith: { a, _ in a })
    let ins = byTitle["Insertar"]
    check(ins?.role == "AXRadioButton" && ins?.path == ["AXWindow:Libro1", "AXGroup:", "AXTabGroup:", "AXRadioButton:Insertar"], "AX path is the role:title chain from the window")
    check(ins?.id == "w.0.0.1" && r.focusedId == "w.0.0.1" && !r.secureFocused, "ids are child-index chains; focus found")
    check(ins?.frame == BBox(x: 318, y: 58, w: 52, h: 24) && ins?.value == "1", "frame (global points) and value sent")
    check(!r.elements.contains { $0.role == "AXGroup" }, "untitled groups aren't sent (but walked through)")
    check(r.elements.contains { $0.role == "AXTabGroup" && $0.title == nil }, "actionable containers are sent untitled")
    check(byTitle["Close"]?.role == "AXButton" && byTitle["Sort ascending"]?.enabled == false, "title falls back to AXDescription, then AXHelp; enabled sent")
    let pw = r.elements.first { $0.title == "Password" }
    check(pw != nil && pw?.value == nil, "secure field: sent without its value")
    check(byTitle["Offscreen"] == nil && byTitle["Hidden child"] == nil, "off-screen elements and their subtrees are skipped")
    check(r.elements.contains { $0.role == "AXStaticText" && $0.value == "Total" && $0.path == ["AXWindow:Libro1", "AXGroup:", "AXStaticText:"] }, "frameless group walked through; value trimmed")
    check(r.elements.first?.role == "AXWindow" && r.elements.first?.id == "w", "the window itself comes first")
    check(AXWalk.redact(r.elements, secureFocused: false, secureInput: false).count == r.elements.count, "nothing redacted normally")

    let s = AXWalk.walk(win, focused: password, visible: visible)
    check(s.secureFocused && s.focusedId == "w.1", "a focused password field is detected")
    check(AXWalk.redact(s.elements, secureFocused: s.secureFocused, secureInput: false).isEmpty, "secure focus → no elements at all")
    check(AXWalk.redact(r.elements, secureFocused: false, secureInput: true).isEmpty, "secure keyboard entry → no elements at all")
    check(AXWalk.isSecure(AXAttrs(role: "AXSecureTextField")) && !AXWalk.isSecure(AXAttrs(role: "AXTextField")), "secure by role or subrole")
    check(AXWalk.value(AXAttrs(role: "AXTextField", subrole: "AXSecureTextField", value: "pw")) == nil, "secure value never comes out")
    check(AXWalk.title(AXAttrs(role: "AXButton", title: "  ", desc: "", help: "Help")) == "Help", "blank titles skipped")
    check(AXWalk.title(AXAttrs(role: "AXButton", title: String(repeating: "x", count: 500)))?.count == AXWalk.titleMax, "titles capped")

    var lim = AXWalk.Limits()
    lim.maxElements = 3
    let small = AXWalk.walk(win, visible: visible, limits: lim)
    check(small.elements.count == 3 && small.truncated, "element budget respected")
    lim = AXWalk.Limits()
    lim.maxDepth = 1
    let shallow = AXWalk.walk(win, visible: visible, limits: lim)
    check(!shallow.elements.contains { $0.title == "Insertar" } && shallow.elements.contains { $0.title == "Close" }, "depth limit")
    // breadth first: shallow controls come before deep ones
    let order = r.elements.map { $0.title ?? $0.role }
    check((order.firstIndex(of: "Close") ?? 99) < (order.firstIndex(of: "Insertar") ?? -1), "breadth first: shallow controls first")
    // a wide table only contributes its first rows
    let cells = (0..<200).map { FakeAX(AXAttrs(role: "AXRow", title: "row \($0)", frame: R(0, 200 + Double($0), 100, 1))) }
    let table = FakeAX(AXAttrs(role: "AXTable", frame: R(0, 200, 800, 400)), cells)
    let tw = AXWalk.walk(FakeAX(AXAttrs(role: "AXWindow", title: "T", frame: visible), [table]), visible: visible)
    check(tw.elements.filter { $0.role == "AXRow" }.count == AXWalk.Limits().maxBulkyChildren && tw.truncated, "bulky containers are capped")

    let deep = AXWalk.focusedElement(AXAttrs(role: "AXTextField", title: "Name", frame: R(10, 10, 100, 20)),
                                     ancestors: [AXAttrs(role: "AXWindow", title: "Libro1"), AXAttrs(role: "AXSheet")])
    check(deep?.id == "focus" && deep?.path == ["AXWindow:Libro1", "AXSheet:", "AXTextField:Name"], "focus beyond the walk is sent with its ancestors' path")
}

// ---------------------------------------------------------------- blocklist, frame maths, recorder, options
do {
    let bl = ["1Password", "Bitwarden", "Keychain Access", "com.example.bank"]
    check(isBlocked(app: "1Password", bundleId: "com.1password.1password", blocklist: bl), "blocklist by name")
    check(isBlocked(app: "1Password 7", bundleId: "x", blocklist: bl), "blocklist: name with a version")
    check(isBlocked(app: "keychain access", bundleId: "", blocklist: bl), "blocklist: case-insensitive")
    check(isBlocked(app: "My Bank", bundleId: "com.example.bank", blocklist: bl), "blocklist by bundle id")
    check(!isBlocked(app: "Microsoft Excel", bundleId: "com.microsoft.Excel", blocklist: bl), "Excel isn't blocked")
    check(!isBlocked(app: "Bitwardenish", bundleId: "", blocklist: bl) && !isBlocked(app: "Excel", bundleId: "", blocklist: ["", " "]), "no partial-word or empty matches")

    let mb = FrameMath.size(points: 1512, 982, backingScale: 2)  // MacBook: 3024 px native → 1280 px
    check(mb.width == 1280 && mb.height == 831 && near(mb.scale, 1280.0 / 1512), "Retina display → 1280 px wide, scale = px per point")
    let small = FrameMath.size(points: 1024, 768, backingScale: 1)
    check(small.width == 1024 && small.height == 768 && near(small.scale, 1), "never upscaled")
    let ext = FrameMath.size(points: 1920, 1080, backingScale: 1, maxWidth: 1280)
    check(ext.width == 1280 && ext.height == 720, "16:9 external keeps its aspect")
    check(FrameMath.size(points: 0, 0, backingScale: 2).width == 0, "empty display")
    let g = FrameMath.toGlobal(px: 640, 415.5, origin: Point2(x: -1920, y: -98), scale: ext.scale)
    check(near(g.x, -960) && near(g.y, -98 + 415.5 * 1.5), "JPEG pixel → global point")

    var saved: [String: Data] = [:]
    let frame = try! Wire.encode(ScreenFrame(id: "f/1", display: 1, origin: Point2(x: 0, y: 0), width: 2, height: 2, scale: 1,
                                             jpegB64: Data([0xFF, 0xD8, 0xFF]).base64EncodedString(), window: nil, tMs: 1, trigger: .tick))
    let line = Recorder.line(for: frame) { saved[$0] = $1 }
    check(saved["f_1.jpg"] == Data([0xFF, 0xD8, 0xFF]) && line.contains("\"jpeg_b64\":\"@f_1.jpg\"") && !line.contains("/9j"), "recorder: frame JPEG to its own file")
    let goal = try! Wire.encode(GuideGoal(text: "hi"))
    check(Recorder.line(for: goal) { _, _ in saved["x"] = Data() } == goal && saved["x"] == nil, "recorder: other messages as sent")

    let o = try? Options.parse(["--record", "/tmp/x", "--no-prompts", "--aec", "--log-ax", "--no-capture", "--smoke"])
    check(o?.record == "/tmp/x" && o?.prompts == false && o?.aec == true && o?.logAX == true && o?.capture == false && o?.smoke == true, "new flags")
    check((try? Options.parse([]))?.prompts == true && (try? Options.parse([]))?.aec == false, "defaults: prompts on, echo cancellation off")
    check((try? Options.parse(["--record"])) == nil, "--record needs a folder")
    check((try? Options.parse(["--server", "8034"]))?.serverLabel == "127.0.0.1:8034", "server label for messages")
}

print("overlay-selftest: \(checks - failures)/\(checks) checks passed")
exit(failures == 0 ? 0 : 1)
