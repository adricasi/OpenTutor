import AVFAudio
import Foundation

// Voice on the Mac, mirroring the web client (apps/web/src/voice/voice.ts): the mic goes out as 16 kHz mono PCM16
// in ~100 ms `voice.audio` frames; the tutor's replies come back as PCM16 at their own rate. Barge-in is decided
// here: as soon as the mic hears the user while the tutor is talking, playback stops and `voice.stop` goes out.
// Pure DSP only (no devices), so overlay-selftest can check it.

public enum VoiceFormat {
    public static let rate = 16000.0
    public static let chunkMs = 100.0
    public static var chunkSamples: Int { Int(rate * chunkMs / 1000) }  // 1600
    public static let frameMs = 20.0  // barge-in / level frames
    public static var frameSamples: Int { Int(rate * frameMs / 1000) }  // 320
}

/// Float samples (-1…1) ↔ little-endian PCM16, the wire format of `voice.audio`.
public enum PCM16 {
    public static func encode(_ x: [Float]) -> Data {
        var out = Data(count: x.count * 2)
        out.withUnsafeMutableBytes { raw in
            let p = raw.bindMemory(to: Int16.self)
            for (i, v) in x.enumerated() {
                let c = max(-1, min(1, v.isFinite ? v : 0))
                p[i] = Int16((c * 32767).rounded()).littleEndian
            }
        }
        return out
    }

    public static func decode(_ d: Data) -> [Float] {
        let n = d.count / 2
        var out = [Float](repeating: 0, count: n)
        d.withUnsafeBytes { raw in
            for i in 0..<n {
                let lo = UInt16(raw[2 * i]), hi = UInt16(raw[2 * i + 1])
                out[i] = Float(Int16(bitPattern: lo | hi << 8)) / 32768
            }
        }
        return out
    }

    public static func base64(_ x: [Float]) -> String { encode(x).base64EncodedString() }
}

public func rms<C: Collection>(_ x: C) -> Float where C.Element == Float {
    guard !x.isEmpty else { return 0 }
    var e: Float = 0
    for v in x { e += v * v }
    return (e / Float(x.count)).squareRoot()
}

/// Mic RMS → 0…1 for the level ring (log scale: −60 dBFS silent, −10 dBFS full), like the web's `levelFraction`.
public func levelFraction(_ rms: Float) -> Double {
    guard rms > 0 else { return 0 }
    return max(0, min(1, (20 * log10(Double(rms)) + 60) / 50))
}

/// Any input format (the mic's 44.1/48 kHz, 1–n channels) → 16 kHz mono float32, with AVAudioConverter.
/// Feed it buffers in order; it keeps the filter state between them, so the stream has no seams.
public final class Resampler {
    public let input: AVAudioFormat
    public let output: AVAudioFormat
    private let converter: AVAudioConverter

    public init?(input: AVAudioFormat, rate: Double = VoiceFormat.rate) {
        guard let out = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: rate, channels: 1, interleaved: false),
            let conv = AVAudioConverter(from: input, to: out)
        else { return nil }
        conv.downmix = true  // a stereo / multi-mic input is mixed to mono, not cut to its first channel
        conv.sampleRateConverterQuality = AVAudioQuality.medium.rawValue
        self.input = input
        self.output = out
        self.converter = conv
    }

    /// The 16 kHz samples for one input buffer (a few may come out with the next one: filter delay).
    public func convert(_ buffer: AVAudioPCMBuffer) -> [Float] {
        let cap = AVAudioFrameCount(Double(buffer.frameLength) * output.sampleRate / input.sampleRate) + 64
        guard buffer.frameLength > 0, let out = AVAudioPCMBuffer(pcmFormat: output, frameCapacity: cap) else { return [] }
        var fed = false
        var err: NSError?
        let status = converter.convert(to: out, error: &err) { _, inStatus in
            if fed {
                inStatus.pointee = .noDataNow
                return nil
            }
            fed = true
            inStatus.pointee = .haveData
            return buffer
        }
        guard status != .error, let ch = out.floatChannelData else { return [] }
        return Array(UnsafeBufferPointer(start: ch[0], count: Int(out.frameLength)))
    }

    /// A float32 buffer of `samples` (one channel per array) at `rate`: for tests and for playback.
    public static func buffer(_ channels: [[Float]], rate: Double) -> AVAudioPCMBuffer? {
        guard let first = channels.first,
            let fmt = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: rate, channels: AVAudioChannelCount(channels.count), interleaved: false),
            let b = AVAudioPCMBuffer(pcmFormat: fmt, frameCapacity: AVAudioFrameCount(max(1, first.count)))
        else { return nil }
        b.frameLength = AVAudioFrameCount(first.count)
        for (c, x) in channels.enumerated() {
            x.withUnsafeBufferPointer { src in b.floatChannelData![c].update(from: src.baseAddress!, count: x.count) }
        }
        return b
    }
}

/// Collects 16 kHz samples into fixed-size chunks (100 ms = 1600 samples) for `voice.audio`.
public struct Chunker {
    public let size: Int
    private var pending: [Float] = []

    public init(size: Int = VoiceFormat.chunkSamples) { self.size = size }

    public mutating func push(_ x: [Float]) -> [[Float]] {
        pending.append(contentsOf: x)
        var out: [[Float]] = []
        while pending.count >= size {
            out.append(Array(pending.prefix(size)))
            pending.removeFirst(size)
        }
        return out
    }

    public mutating func reset() { pending = [] }
    public var buffered: Int { pending.count }
}

/// Barge-in detector (the web's `BargeIn`): RMS above an adaptive noise floor for `need` consecutive 20 ms frames.
/// `minRms` is raised while the tutor talks without echo cancellation, so its own voice through the speakers
/// doesn't cut it off.
public struct BargeIn {
    public var need: Int
    public var ratio: Float
    public var minRms: Float = 0.015
    public private(set) var floor: Float = 0.01
    public private(set) var run = 0
    public private(set) var rms: Float = 0

    public init(need: Int = 3, ratio: Float = 3.5) { self.need = need; self.ratio = ratio }

    /// One ~20 ms frame; true exactly once when speech starts.
    public mutating func frame<C: Collection>(_ x: C) -> Bool where C.Element == Float {
        let r = OverlayCore.rms(x)
        rms = r
        let loud = r > max(minRms, floor * ratio)
        if !loud { floor = 0.95 * floor + 0.05 * r }  // track the background while quiet
        run = loud ? run + 1 : 0
        return run == need
    }

    /// A frame that is known not to be the user (the first moments of the tutor's own voice through the speakers):
    /// the floor follows it quickly, so only a voice clearly louder than the echo counts as speech afterwards.
    public mutating func learn<C: Collection>(_ x: C) where C.Element == Float {
        let r = OverlayCore.rms(x)
        rms = r
        floor = max(floor, 0.6 * floor + 0.4 * r)
        run = 0
    }

    public mutating func reset() { run = 0 }
}

/// The mic pipeline after resampling: 20 ms frames → barge-in + level; 100 ms chunks → the server.
public struct MicPipeline {
    public var barge = BargeIn()
    public var learning = false  // frames teach the noise floor instead of detecting speech (see BargeIn.learn)
    private var chunker = Chunker()
    private var frame: [Float] = []
    private var peak: Float = 0

    public struct Out {
        public var chunks: [[Float]] = []  // 100 ms each
        public var levels: [Float] = []  // the loudest 20 ms frame of each chunk (RMS)
        public var speechStarted = false
    }

    public init() {}

    public mutating func push(_ x: [Float]) -> Out {
        var o = Out()
        for v in x {
            frame.append(v)
            if frame.count >= VoiceFormat.frameSamples {
                if learning { barge.learn(frame) } else if barge.frame(frame) { o.speechStarted = true }
                peak = max(peak, barge.rms)
                frame.removeAll(keepingCapacity: true)
            }
        }
        for c in chunker.push(x) {
            o.chunks.append(c)
            o.levels.append(peak)
            peak = 0
        }
        return o
    }

    public mutating func reset() {
        chunker.reset()
        frame = []
        peak = 0
        barge.reset()
    }
}
