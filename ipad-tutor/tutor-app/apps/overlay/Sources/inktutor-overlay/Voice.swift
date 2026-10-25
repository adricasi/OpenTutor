import AVFoundation
import OverlayCore

/// Mic and speaker on one AVAudioEngine. By default the mic is half duplex: while the tutor talks, nothing goes to
/// the server (it would transcribe the tutor itself), the first 250 ms of playback teach the barge-in detector the
/// speakers' echo level, and only a voice clearly louder than that echo barges in. `--aec` tries macOS voice
/// processing (echo cancellation, like the browser's `echoCancellation`) instead; on the reference Mac (macOS 14.3)
/// it hands back pure silence, so the helper checks the first 1.5 s and falls back by itself.
///
/// - mic: input tap → AVAudioConverter to 16 kHz mono → PCM16 chunks of 100 ms (`onChunk`), the loudest 20 ms
///   frame's RMS per chunk (`onLevel`, for the ring), barge-in while the tutor talks (`onBargeIn`).
/// - speaker: `voice.audio` chunks (PCM16 at their own rate) queued back-to-back on an AVAudioPlayerNode.
///
/// The engine runs with an input only while Listen is on: turning Listen off throws the engine away (so the mic,
/// and macOS's orange mic dot, go off) and a playback-only engine takes its place. Every callback is on main.
final class VoiceIO {
    var onChunk: (String) -> Void = { _ in }
    var onLevel: (Float) -> Void = { _ in }
    var onPlaying: (Bool) -> Void = { _ in }
    var onBargeIn: () -> Void = {}
    var onRestart: (String) -> Void = { _ in }  // the audio device changed under us

    private let wantAEC: Bool
    private var engine = AVAudioEngine()
    private var player = AVAudioPlayerNode()
    private var playerRate: Double = 0
    private var pipeline = MicPipeline()
    private(set) var micOn = false
    private(set) var aecActive = false
    private(set) var playing = false
    private var pending = 0
    private var gen = 0  // bumps on stop: completion callbacks of flushed buffers are ignored
    private var playStarted = 0.0
    private var configObserver: NSObjectProtocol?
    private var aecChecked = false
    private var aecChunks = 0
    private var aecBroken = false  // this run already found voice processing silent
    private var openedAt = 0.0  // voice processing reconfigures the device itself right after start: not a change
    static let bargeGrace = 0.25  // s after playback starts during which the mic can't barge in (speaker onset)

    init(aec: Bool) {
        wantAEC = aec
        freshEngine()
    }

    static var micStatus: AVAuthorizationStatus { AVCaptureDevice.authorizationStatus(for: .audio) }

    static let micDeniedText = "Microphone access is off for the helper — menu → Allow microphone…, or System Settings → Privacy & Security → Microphone"

    // ---- mic
    /// Starts the mic, asking for permission first if needed; `done(nil)` on success, else a message for the user.
    func startMic(_ done: @escaping (String?) -> Void) {
        switch Self.micStatus {
        case .authorized:
            done(openMic())
        case .notDetermined:
            AVCaptureDevice.requestAccess(for: .audio) { granted in
                DispatchQueue.main.async { done(granted ? self.openMic() : Self.micDeniedText) }
            }
        default:
            done(Self.micDeniedText)
        }
    }

    func stopMic() {
        guard micOn else { return }
        micOn = false
        stopPlayback()
        engine.inputNode.removeTap(onBus: 0)
        engine.stop()
        freshEngine()  // drop the input unit entirely: the mic is really off
        pipeline.reset()
    }

    private func openMic() -> String? {
        if micOn { return nil }
        stopPlayback()
        engine.stop()
        freshEngine()
        aecChecked = false
        aecChunks = 0
        if let err = tryOpen(aec: wantAEC && !aecBroken) {
            guard wantAEC && !aecBroken else { return err }
            print("voice: echo cancellation unavailable (\(err)); mic without it")
            engine.inputNode.removeTap(onBus: 0)
            engine.stop()
            freshEngine()
            if let err2 = tryOpen(aec: false) { return err2 }
        }
        micOn = true
        openedAt = ProcessInfo.processInfo.systemUptime
        pipeline.reset()
        print("voice: mic on (\(aecActive ? "echo cancellation" : "no echo cancellation: muted while the tutor talks"))")
        return nil
    }

    private func tryOpen(aec: Bool) -> String? {
        let input = engine.inputNode
        aecActive = false
        if aec {
            do {
                try input.setVoiceProcessingEnabled(true)
                aecActive = true
                input.voiceProcessingOtherAudioDuckingConfiguration = .init(enableAdvancedDucking: false, duckingLevel: .min)
            } catch {
                return "voice processing: \(error.localizedDescription)"
            }
        }
        let fmt = input.outputFormat(forBus: 0)
        guard fmt.sampleRate > 0, fmt.channelCount > 0 else { return "No microphone found (check System Settings → Sound → Input)" }
        guard let rs = Resampler(input: fmt) else { return "Can't convert the microphone's audio (\(fmt))" }
        if aecActive {  // with voice processing the tap only runs while the input is pulled: a muted sink does it
            let sink = AVAudioMixerNode()
            engine.attach(sink)
            engine.connect(input, to: sink, format: fmt)
            engine.connect(sink, to: engine.mainMixerNode, format: nil)
            sink.outputVolume = 0
        }
        let bufferSize = AVAudioFrameCount(fmt.sampleRate * 0.05)  // ~50 ms taps; chunks are cut at 100 ms anyway
        input.installTap(onBus: 0, bufferSize: bufferSize, format: fmt) { [weak self] buf, _ in
            let x = rs.convert(buf)  // on the tap's thread, in order
            guard !x.isEmpty else { return }
            DispatchQueue.main.async { self?.process(x) }
        }
        connectPlayer(rate: playerRate > 0 ? playerRate : 24000)
        engine.prepare()
        do {
            try engine.start()
        } catch {
            input.removeTap(onBus: 0)
            return "The microphone didn't start: \(error.localizedDescription)"
        }
        return nil
    }

    private func process(_ x: [Float]) {
        guard micOn else { return }
        let now = ProcessInfo.processInfo.systemUptime
        pipeline.barge.minRms = playing && !aecActive ? 0.05 : 0.015
        pipeline.learning = playing && now - playStarted < Self.bargeGrace  // learn the speakers' echo first
        let out = pipeline.push(x)
        if aecActive, !aecChecked {  // voice processing that hands back pure silence (seen on macOS 14): fall back
            aecChunks += out.chunks.count
            if out.levels.contains(where: { $0 > 0 }) { aecChecked = true }
            else if aecChunks >= 15 {
                aecChecked = true
                print("voice: echo cancellation gives a silent mic here; reopening the mic without it")
                reopenWithoutAEC()
                return
            }
        }
        if out.speechStarted, playing {
            stopPlayback()
            onBargeIn()
        }
        for (chunk, level) in zip(out.chunks, out.levels) {
            onLevel(level)
            if playing && !aecActive { continue }  // half duplex: the server would transcribe the tutor itself
            onChunk(PCM16.base64(chunk))
        }
    }

    private func reopenWithoutAEC() {
        aecBroken = true
        micOn = false
        engine.inputNode.removeTap(onBus: 0)
        engine.stop()
        freshEngine()
        if let err = openMic() { onRestart(err) }
    }

    // ---- speaker
    func play(pcm16B64: String, rate: Int) {
        guard let data = Data(base64Encoded: pcm16B64), rate > 0 else { return }
        let x = PCM16.decode(data)
        guard !x.isEmpty, let buf = Resampler.buffer([x], rate: Double(rate)) else { return }
        if Double(rate) != playerRate {
            stopPlayback()
            connectPlayer(rate: Double(rate))
        }
        if !engine.isRunning {
            engine.prepare()
            do { try engine.start() } catch {
                print("voice: playback didn't start: \(error.localizedDescription)")
                return
            }
        }
        pending += 1
        let g = gen
        player.scheduleBuffer(buf, completionCallbackType: .dataPlayedBack) { [weak self] _ in
            DispatchQueue.main.async {
                guard let self, g == self.gen else { return }
                self.pending = max(0, self.pending - 1)
                if self.pending == 0 { self.setPlaying(false) }
            }
        }
        if !player.isPlaying { player.play() }
        if !playing { playStarted = ProcessInfo.processInfo.systemUptime }
        setPlaying(true)
    }

    func stopPlayback() {
        gen += 1
        pending = 0
        if engine.isRunning || player.isPlaying { player.stop() }
        setPlaying(false)
    }

    private func setPlaying(_ on: Bool) {
        guard on != playing else { return }
        playing = on
        if !on { pipeline.barge.reset() }
        onPlaying(on)
    }

    // ---- engine plumbing
    private func freshEngine() {
        if let o = configObserver { NotificationCenter.default.removeObserver(o) }
        engine = AVAudioEngine()
        player = AVAudioPlayerNode()
        engine.attach(player)
        playerRate = 0
        connectPlayer(rate: 24000)
        configObserver = NotificationCenter.default.addObserver(
            forName: .AVAudioEngineConfigurationChange, object: engine, queue: .main
        ) { [weak self] _ in self?.configurationChanged() }
    }

    private func connectPlayer(rate: Double) {
        guard rate != playerRate, let fmt = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: rate, channels: 1, interleaved: false) else { return }
        engine.disconnectNodeOutput(player)
        engine.connect(player, to: engine.mainMixerNode, format: fmt)
        playerRate = rate
    }

    /// Headphones plugged in, a different input chosen…: AVAudioEngine stops itself. Reopen the mic if it was on.
    private func configurationChanged() {
        if ProcessInfo.processInfo.systemUptime - openedAt < 1.5 {
            if !engine.isRunning { try? engine.start() }
            return
        }
        let wasOn = micOn
        print("voice: audio device changed\(wasOn ? "; reopening the mic" : "")")
        micOn = false
        if wasOn { engine.inputNode.removeTap(onBus: 0) }  // (touching inputNode on a playback engine would open the mic)
        engine.stop()
        freshEngine()
        gen += 1
        pending = 0
        setPlaying(false)
        if wasOn {
            if let err = openMic() { onRestart(err) }
        }
    }
}
