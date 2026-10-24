import AppKit
import ApplicationServices
import OverlayCore

/// Wires the pieces: socket ⇄ overlay, phantom icon (status, bubble, menu, transcript), voice, perception.
/// Every input goes through `StatusModel` (OverlayCore), which decides the icon and the bubble; `refresh()` draws it.
final class AppDelegate: NSObject, NSApplicationDelegate {
    let options: Options
    private var overlay: OverlayController!
    private var phantom: PhantomController!
    private var socket: ScreenSocket!
    private var voice: VoiceIO!
    private var perception: Perception!
    private var recorder: Recorder?
    private var status: StatusModel
    private var log = TranscriptLog()
    private var signalSources: [DispatchSourceSignal] = []
    private var tick: Timer?
    private var ticks = 0
    private var listening = false  // what the user chose; survives reconnects and pauses
    private var muted = false  // the tutor's voice isn't played
    private var micOn = false
    private var level = 0.0
    private var paused = false
    private var everConnected = false
    private var runtime: RuntimeStatus?
    private var lastRuntimeEvent = 0
    private var missing: [Permission] = []
    private var shown = ""  // last icon + bubble printed
    private var peakLevel = 0.0
    private var smokeStarted = false

    init(options: Options) {
        self.options = options
        status = StatusModel(server: options.serverLabel)
    }

    private var now: Double { ProcessInfo.processInfo.systemUptime }

    func applicationDidFinishLaunching(_ notification: Notification) {
        let url = try! options.url()  // validated by Options.parse
        overlay = OverlayController(logFrames: options.logFrames)
        socket = ScreenSocket(url: url)
        phantom = PhantomController()
        voice = VoiceIO(aec: options.aec)
        perception = Perception(log: options.logAX)
        if let dir = options.record {
            do {
                let r = try Recorder(dir: dir)
                recorder = r
                socket.onSend = { r.record($0) }
                print("recording every message sent to \(r.dir.path)/messages.jsonl (frames as .jpg)")
            } catch {
                print("--record \(dir): \(error.localizedDescription)")
            }
        }

        overlay.onCaption = { [weak self] text in self?.addLog(.tutor, text) }
        overlay.onPhantomState = { [weak self] s in
            self?.status.serverPhantom(s)
            self?.refresh()
        }
        socket.helloProvider = { [weak self] in
            ScreenHello(helper: helperName, displays: self?.overlay.space.displayInfos() ?? [])
        }
        socket.onConnected = { [weak self] on in self?.connectionChanged(on) }
        socket.onMessage = { [weak self] msg in self?.handle(msg) }

        phantom.menuState = { [weak self] in self?.menuState() ?? MenuState() }
        phantom.onListen = { [weak self] on in self?.setListening(on) }
        phantom.onMute = { [weak self] on in self?.setMuted(on) }
        phantom.onControl = { [weak self] a in self?.control(a) }
        phantom.onPause = { [weak self] in self?.togglePause() }
        phantom.onExit = { [weak self] in self?.exitHelper() }
        phantom.onPermission = { [weak self] p in
            p.request { [weak self] note in
                guard let self else { return }
                status.note(note, now: now, ttl: 8)
                addLog(.status, note)
                checkPermissions()
                refresh()
            }
        }

        voice.onChunk = { [weak self] b64 in
            guard let self, !paused, listening else { return }
            socket.send(VoiceAudio(pcm16B64: b64))
        }
        voice.onLevel = { [weak self] rms in
            guard let self else { return }
            level = 0.5 * level + 0.5 * levelFraction(rms)
            peakLevel = max(peakLevel, level)
            phantom.setLevel(level)
        }
        voice.onPlaying = { [weak self] on in
            guard let self else { return }
            status.setSpeaking(on, now: now)
            refresh()
        }
        voice.onBargeIn = { [weak self] in
            guard let self else { return }
            socket.send(VoiceStop())
            addLog(.status, "You talked over the tutor: it stopped")
        }
        voice.onRestart = { [weak self] err in
            guard let self else { return }
            micOn = false
            listening = false
            socket.send(SessionUpdate(voice: false))
            status.micProblem(err, now: now)
            addLog(.error, err)
            refresh()
        }

        perception.space = { [weak self] in self?.overlay.space ?? ScreenSpace(displays: []) }
        perception.onSnapshot = { [weak self] s in self?.socket.send(s) }
        perception.onFrame = { [weak self] f in self?.socket.send(f) }
        perception.onInput = { [weak self] e in self?.socket.send(e) }
        perception.onBlock = { [weak self] why in
            guard let self else { return }
            status.setCaptureBlock(why, now: now)
            addLog(.status, why ?? "Capture resumed")
            refresh()
        }

        NotificationCenter.default.addObserver(
            forName: NSApplication.didChangeScreenParametersNotification, object: nil, queue: .main
        ) { [weak self] _ in
            guard let self else { return }
            overlay.rebuildWindows()
            phantom.keepOnScreen()
            socket.sendHello()
        }
        for sig in [SIGINT, SIGTERM] {
            signal(sig, SIG_IGN)
            let src = DispatchSource.makeSignalSource(signal: sig, queue: .main)
            src.setEventHandler { [weak self] in self?.quit() }
            src.resume()
            signalSources.append(src)
        }

        checkPermissions()
        print("permissions: " + Permission.allCases.map { "\($0.rawValue) \($0.granted ? "yes" : "no")" }.joined(separator: ", "))
        if options.capture && options.prompts {  // macOS's own prompts, once; the menu offers them again later
            if !Permission.screen.granted { _ = CGRequestScreenCaptureAccess() }
            if !Permission.accessibility.granted {
                _ = AXIsProcessTrustedWithOptions([kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: true] as CFDictionary)
            }
        }
        if options.capture { perception.start() }

        let t = Timer(timeInterval: 0.25, repeats: true) { [weak self] _ in self?.onTick() }
        RunLoop.main.add(t, forMode: .common)  // keeps running while the menu or the Ask box is open
        tick = t

        print("\(helperName): \(overlay.windows.count) overlay window(s); server \(url.absoluteString)")
        status.starting(now: now)
        refresh()
        socket.start()
        if options.smoke { DispatchQueue.main.asyncAfter(deadline: .now() + 3) { [weak self] in self?.runSmoke() } }
    }

    // ---- connection
    private func connectionChanged(_ on: Bool) {
        status.setConnected(on, now: now)
        addLog(on ? .status : .error, on ? "Connected to the app server (\(options.serverLabel))" : status.notConnectedText)
        if on {
            if listening && micOn { socket.send(SessionUpdate(voice: true)) }  // Listen survives a reconnect
            if paused { socket.send(GuideControl(action: .pause)) }
            if !everConnected, !missing.isEmpty, options.capture {
                status.note("Not allowed yet: " + missing.map(\.rawValue).joined(separator: ", ") + " — open the menu to allow", now: now, ttl: 8)
            }
            if !everConnected, options.listenAtStart, !listening { setListening(true) }  // just talk to it
            everConnected = true
        } else {
            voice.stopPlayback()
        }
        perception.setEnabled(on && !paused && options.capture)
        refresh()
    }

    // ---- server → helper
    private func handle(_ msg: ServerMessage) {
        switch msg {
        case .overlay(let m):
            overlay.apply(m.event)
        case .config(let c):
            print("screen.config: blocklist \(c.blocklist.joined(separator: ", ")); capture \(c.captureFps) fps")
            perception.configure(blocklist: c.blocklist, fps: c.captureFps)
        case .plan(let m):
            status.planArrived(steps: m.plan.steps.count, now: now)
            addLog(.status, "Plan for “\(m.plan.goal)”: \(m.plan.steps.count) step\(m.plan.steps.count == 1 ? "" : "s")")
            for s in m.plan.steps { addLog(.step, "\(s.n). \(s.instruction)") }
        case .status(let m):
            status.guideStatus(m, now: now)
            perception.guiding = status.guiding
            let text = status.visibleBubble(now: now)?.text ?? m.detail
            addLog(m.phase == .error ? .error : .status, text)
            if m.phase == .error, m.detail.lowercased().hasPrefix("voice"), micOn || listening { voiceRefused() }
        case .transcript(let t):
            addLog(.you, t.text, partial: !t.isFinal)
            status.heard(t.text, final: t.isFinal, now: now)
        case .audio(let a):
            if !paused && !muted { voice.play(pcm16B64: a.pcm16B64, rate: a.sampleRate) }
        case .runtime(let r):
            runtime = r.status
            if r.status.eventId != lastRuntimeEvent, !r.status.event.isEmpty {
                lastRuntimeEvent = r.status.eventId
                addLog(.status, r.status.event)
                status.note(r.status.event, now: now, ttl: 6)
            }
        case .error(let e):
            status.serverError(e.message, now: now)
            addLog(.error, e.message)
            if e.message.lowercased().hasPrefix("voice"), micOn || listening { voiceRefused() }
        case .unknown(let t):
            if options.logFrames { print("ignored message type \(t)") }
        }
        refresh()
    }

    // ---- phantom actions
    private func ask(_ text: String) {
        addLog(.you, text)
        if socket.isOpen { perception.refreshNow() }  // the planner wants the screen as it is now
        if socket.send(GuideGoal(text: text)) || !status.connected {
            status.askSent(now: now)
        } else {
            status.serverError("Couldn't send: the connection to the app server just dropped", now: now)
        }
        refresh()
    }

    /// Mute: the tutor's voice isn't played (captions and highlights stay); what it would say is still logged.
    private func setMuted(_ on: Bool) {
        muted = on
        if on { voice.stopPlayback() }
        status.note(on ? "Muted — captions only" : "Sound on", now: now, ttl: 3)
        addLog(.status, on ? "Muted" : "Sound on")
        refresh()
    }

    private func setListening(_ on: Bool) {
        if on {
            listening = true
            status.setListening(true, now: now)  // listening at once; the mic follows
            refresh()
            guard !paused else { return }
            openMic()
        } else {
            listening = false
            if micOn { voice.stopMic() }
            micOn = false
            level = 0
            socket.send(SessionUpdate(voice: false))
            status.setListening(false, now: now)
            addLog(.status, "Listen off")
            refresh()
        }
    }

    private func openMic() {
        voice.startMic { [weak self] err in
            guard let self else { return }
            if let err {
                listening = false
                micOn = false
                status.micProblem(err, now: now)
                addLog(.error, err)
                checkPermissions()
            } else if listening && !paused {
                micOn = true
                socket.send(SessionUpdate(voice: true))
                addLog(.status, "Listening")
            } else {
                voice.stopMic()  // turned off (or paused) while the permission prompt was up
            }
            refresh()
        }
    }

    private func control(_ a: GuideAction) {
        if a == .stop { voice.stopPlayback() }  // Cancel: silent at once, whatever the server is doing
        if socket.send(GuideControl(action: a)) {
            addLog(.status, ["next": "Next step", "back": "Previous step", "repeat": "Repeat the step", "stop": "Cancelled"][a.rawValue] ?? a.rawValue)
            if a != .stop { status.askSent(now: now) }
        } else {
            status.note(status.notConnectedText + " — start it with make ipad", error: true, now: now, ttl: 8)
        }
        refresh()
    }

    private func togglePause() {
        paused.toggle()
        socket.send(GuideControl(action: paused ? .pause : .resume))
        overlay.setHidden(paused)
        phantom.bringToFront()
        status.setPaused(paused, now: now)
        perception.setEnabled(socket.isOpen && !paused && options.capture)
        if paused {
            voice.stopPlayback()
            if micOn { voice.stopMic() }
            micOn = false
        } else if listening {
            openMic()
        }
        addLog(.status, paused ? "Paused" : "Resumed")
        refresh()
    }

    /// The server can't listen (no speech models): turn the mic off without telling it again; the message stays up.
    private func voiceRefused() {
        if micOn { voice.stopMic() }
        micOn = false
        listening = false
        level = 0
    }

    private func exitHelper() {
        socket.send(GuideControl(action: .stop))
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) { [weak self] in self?.quit() }
    }

    private func quit() {
        voice?.stopMic()
        recorder?.flush()
        socket?.close()
        print("\(helperName): bye")
        NSApp.terminate(nil)
    }

    // ---- state → screen
    private func onTick() {
        ticks += 1
        status.tick(now: now)
        if ticks % 8 == 0 { checkPermissions() }
        if ticks % 8 == 0, micOn, options.logAX || options.smoke {
            print("mic level: peak \(String(format: "%.2f", peakLevel)) over the last 2 s")
            peakLevel = 0
        }
        refresh()
    }

    /// `--smoke`: what a person would do by hand, without anyone clicking.
    private func runSmoke() {
        guard !smokeStarted else { return }
        smokeStarted = true
        print("smoke: asking a test goal now, Listen on at +8 s, off at +14 s")
        ask("smoke test: show me the Insert tab")
        DispatchQueue.main.asyncAfter(deadline: .now() + 8) { [weak self] in self?.setListening(true) }
        DispatchQueue.main.asyncAfter(deadline: .now() + 14) { [weak self] in self?.setListening(false) }
    }

    private func checkPermissions() {
        let m = Permission.missing.filter { options.capture || $0 == .microphone }
        if m != missing {
            if !missing.isEmpty || ticks > 0 {
                print("permissions: " + Permission.allCases.map { "\($0.rawValue) \($0.granted ? "yes" : "no")" }.joined(separator: ", "))
            }
            missing = m
            perception?.permissionsChanged()
        }
    }

    private func refresh() {
        let icon = status.icon(now: now)
        let tip = "Ink Tutor — \(icon.rawValue) · \(socket.isOpen ? "connected to" : "waiting for") \(options.serverLabel)"
        let bubble = status.visibleBubble(now: now)
        phantom.render(
            icon: icon, level: level, micOn: micOn, capturing: perception.capturing, bubble: bubble,
            said: status.visibleYouSaid(now: now), tooltip: tip
        )
        let line = "\(icon.rawValue) · \(bubble?.text ?? "(no bubble)")"
        if line != shown {
            shown = line
            print("phantom: \(line)")
        }
    }

    private func menuState() -> MenuState {
        var m = MenuState()
        m.connected = socket.isOpen
        m.server = options.serverLabel
        m.listening = listening
        m.muted = muted
        let icon = status.icon(now: now)
        m.busy = icon == .thinking || icon == .speaking || status.guiding
        m.paused = paused
        if status.guiding {
            m.step = status.step
            m.of = status.of
        }
        if let r = runtime, r.level != .ok {
            m.memoryNote = "Memory \(r.level == .critical ? "critical" : "low") — \(String(format: "%.1f", r.availableGb)) GB free (tutor: \(r.tier2Mode))"
        }
        m.missing = missing
        m.transcriptVisible = phantom.transcript.isVisible
        return m
    }

    private func addLog(_ who: TranscriptLog.Who, _ text: String, partial: Bool = false) {
        let t = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !t.isEmpty else { return }
        log.add(who, t, t: Date().timeIntervalSince1970, partial: partial)
        if !partial { print("\(who.rawValue.lowercased()): \(t)") }
        phantom.transcript.render(log)
    }
}
