// Voice in the browser: mic → 16 kHz PCM16 chunks to the server; tutor speech ← PCM chunks, played gaplessly.
// Barge-in is decided here, not on the server: as soon as the mic hears the user while the tutor is talking,
// playback stops (≤ 3 frames ≈ 60 ms) and the server is told to stop too.

export const TARGET_RATE = 16000;
const CHUNK_MS = 100;

/** Downsample float32 audio to 16 kHz (box filter; good enough for speech) and convert to PCM16. */
export function toPcm16(input: Float32Array, fromRate: number): Int16Array {
  const ratio = fromRate / TARGET_RATE;
  const n = Math.floor(input.length / ratio);
  const out = new Int16Array(n);
  for (let i = 0; i < n; i++) {
    const a = Math.floor(i * ratio), b = Math.min(input.length, Math.floor((i + 1) * ratio));
    let sum = 0;
    for (let j = a; j < b; j++) sum += input[j]!;
    const v = Math.max(-1, Math.min(1, sum / Math.max(1, b - a)));
    out[i] = Math.round(v * 32767);
  }
  return out;
}

export function b64(bytes: Uint8Array): string {
  let s = "";
  for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(s);
}

/** Barge-in detector: RMS above an adaptive noise floor for `need` consecutive frames. */
export class BargeIn {
  private floor = 0.01;
  private run = 0;
  /** RMS of the last frame (the level meter shows it). */
  rms = 0;
  constructor(private need = 3, private ratio = 3.5) {}
  /** Feed one ~20 ms frame; returns true when speech starts. */
  frame(x: Float32Array): boolean {
    let e = 0;
    for (const v of x) e += v * v;
    const rms = Math.sqrt(e / Math.max(1, x.length));
    this.rms = rms;
    const loud = rms > Math.max(0.015, this.floor * this.ratio);
    if (!loud) this.floor = 0.95 * this.floor + 0.05 * rms; // track background noise while quiet
    this.run = loud ? this.run + 1 : 0;
    return this.run === this.need;
  }
}

/** Mic RMS → 0..1 for the level meter (log scale: −60 dBFS is silent, −10 dBFS is full). */
export function levelFraction(rms: number): number {
  if (!(rms > 0)) return 0;
  return Math.max(0, Math.min(1, (20 * Math.log10(rms) + 60) / 50));
}

// Events that count as a user gesture for audio in WebKit and Chromium (pen and finger lift, mouse press, keys).
const GESTURES = ["pointerdown", "pointerup", "touchend", "click", "keydown"] as const;

/** iOS keeps an AudioContext "suspended" until a tap resumes it, and puts it in "interrupted" after a call, Siri or
 *  another app took the audio. Resume it inside the next gesture anywhere on the page. Returns a function that disarms it. */
export function resumeOnNextTap(ctx: AudioContext, done: () => void = () => {}): () => void {
  const off = () => GESTURES.forEach((t) => document.removeEventListener(t, go, true));
  const go = () => {
    if (ctx.state === "closed") { off(); return; }
    if (ctx.state === "running") { off(); done(); return; }
    ctx.resume().then(() => { if (ctx.state === "running") { off(); done(); } }, () => { /* try again on the next tap */ });
  };
  GESTURES.forEach((t) => document.addEventListener(t, go, true));
  return off;
}

/** Plays PCM16 chunks back-to-back; stop() silences immediately.
 *  iOS Safari: call unlock() synchronously inside the tap that turns voice on, or playback stays silent. */
export class Player {
  private ctx: AudioContext | null = null;
  private at = 0;
  private sources = new Set<AudioBufferSourceNode>();
  private disarm: (() => void) | null = null;
  private wasBlocked = false;
  onPlaying: (playing: boolean) => void = () => {};
  /** true while the browser holds the sound back until the next tap (suspended / interrupted context). */
  onBlocked: (blocked: boolean) => void = () => {};

  get playing(): boolean { return this.sources.size > 0; }
  get blocked(): boolean { return this.wasBlocked; }

  /** Create or resume the output context inside a user gesture, and play one silent sample (older iOS needs it). */
  unlock(): void {
    const ctx = this.context();
    if (ctx.state !== "running") ctx.resume().catch(() => { /* not a gesture: the next tap retries */ });
    const src = ctx.createBufferSource();
    src.buffer = ctx.createBuffer(1, 1, ctx.sampleRate);
    src.connect(ctx.destination);
    src.start(0);
  }

  play(pcm16b64: string, rate: number): void {
    const ctx = this.context();
    if (ctx.state !== "running") ctx.resume().catch(() => { /* blocked until the next tap */ });
    this.watch();
    const bytes = Uint8Array.from(atob(pcm16b64), (c) => c.charCodeAt(0));
    const pcm = new Int16Array(bytes.buffer, bytes.byteOffset, bytes.byteLength / 2);
    const buf = ctx.createBuffer(1, pcm.length, rate);
    const ch = buf.getChannelData(0);
    for (let i = 0; i < pcm.length; i++) ch[i] = pcm[i]! / 32768;
    const src = ctx.createBufferSource();
    src.buffer = buf;
    src.connect(ctx.destination);
    this.at = Math.max(this.at, ctx.currentTime + 0.02);
    src.start(this.at);
    this.at += buf.duration;
    this.sources.add(src);
    this.onPlaying(true);
    src.onended = () => { this.sources.delete(src); if (!this.playing) this.onPlaying(false); };
  }

  stop(): void {
    for (const s of this.sources) { try { s.stop(); } catch { /* already stopped */ } }
    this.sources.clear();
    if (this.ctx) this.at = this.ctx.currentTime;
    this.onPlaying(false);
  }

  /** Stop and release the context; the next unlock()/play() makes a new one. */
  dispose(): void {
    this.stop();
    this.disarm?.(); this.disarm = null;
    void this.ctx?.close().catch(() => {});
    this.ctx = null; this.at = 0;
    if (this.wasBlocked) { this.wasBlocked = false; this.onBlocked(false); }
  }

  private context(): AudioContext {
    if (!this.ctx || this.ctx.state === "closed") {
      const ctx = new AudioContext();
      ctx.onstatechange = () => { if (this.ctx === ctx) this.watch(); };
      this.ctx = ctx; this.at = 0;
    }
    return this.ctx;
  }

  /** Suspended or interrupted → resume on the next tap, and tell the UI ("tap to hear"). */
  private watch(): void {
    const ctx = this.ctx;
    const blocked = !!ctx && ctx.state !== "running" && ctx.state !== "closed";
    if (blocked && !this.disarm && ctx) this.disarm = resumeOnNextTap(ctx, () => { this.disarm = null; this.watch(); });
    if (!blocked && this.disarm) { this.disarm(); this.disarm = null; }
    if (blocked !== this.wasBlocked) { this.wasBlocked = blocked; this.onBlocked(blocked); }
  }
}

const WORKLET = `class Tap extends AudioWorkletProcessor {
  process(inputs) { const ch = inputs[0] && inputs[0][0]; if (ch) this.port.postMessage(ch.slice(0)); return true; }
}
registerProcessor("tap", Tap);`;

/** Mic capture: calls `onChunk(pcm16 base64)` every ~100 ms, `onLevel(rms)` with each chunk (the loudest 20 ms frame)
 *  and `onBargeIn()` when the user starts talking. */
export class Mic {
  private ctx: AudioContext | null = null;
  private stream: MediaStream | null = null;
  private pending: Float32Array[] = [];
  private pendingLen = 0;
  private peak = 0;
  private frame: number[] = [];
  private barge = new BargeIn();
  private disarm: (() => void) | null = null;

  constructor(
    private onChunk: (b64pcm: string) => void,
    private onBargeIn: () => void,
    private onLevel: (rms: number) => void = () => {},
  ) {}

  static available(): string | null {
    if (!window.isSecureContext) return "Voice needs a secure page (HTTPS). On this Mac open http://localhost:8000; on the iPad it needs HTTPS (ask in Settings).";
    if (!navigator.mediaDevices?.getUserMedia) return "This browser has no microphone access.";
    return null;
  }

  /** The AudioContext is made before the first await, so it starts inside the tap that called start() (iOS). */
  async start(): Promise<void> {
    const ctx = new AudioContext();
    this.ctx = ctx;
    if (ctx.state !== "running") ctx.resume().catch(() => { /* resumed on the next tap */ });
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, channelCount: 1 } });
      if (this.ctx !== ctx) { stream.getTracks().forEach((t) => t.stop()); throw new DOMException("Voice was turned off", "AbortError"); }
      this.stream = stream;
      const url = URL.createObjectURL(new Blob([WORKLET], { type: "application/javascript" }));
      await ctx.audioWorklet.addModule(url);
      if (this.ctx !== ctx) throw new DOMException("Voice was turned off", "AbortError");
      const node = new AudioWorkletNode(ctx, "tap");
      ctx.createMediaStreamSource(stream).connect(node);
      ctx.onstatechange = () => this.watch(ctx);
      this.watch(ctx);
      const rate = ctx.sampleRate;
      node.port.onmessage = (e: MessageEvent<Float32Array>) => this.take(e.data, rate);
    } catch (e) {
      if (this.ctx === ctx) this.stop();
      throw e;
    }
  }

  stop(): void {
    this.stream?.getTracks().forEach((t) => t.stop());
    this.disarm?.(); this.disarm = null;
    void this.ctx?.close().catch(() => {});
    this.ctx = null; this.stream = null;
    this.pending = []; this.pendingLen = 0; this.peak = 0; this.frame = [];
  }

  /** One block from the worklet: 20 ms frames → barge-in + level; 100 ms chunks → the server. */
  private take(x: Float32Array, rate: number): void {
    const frameLen = Math.round(rate * 0.02);
    for (const v of x) {
      this.frame.push(v);
      if (this.frame.length >= frameLen) {
        if (this.barge.frame(Float32Array.from(this.frame))) this.onBargeIn();
        this.peak = Math.max(this.peak, this.barge.rms);
        this.frame = [];
      }
    }
    this.pending.push(x);
    this.pendingLen += x.length;
    if (this.pendingLen >= rate * CHUNK_MS / 1000) {
      const all = new Float32Array(this.pendingLen);
      let o = 0;
      for (const p of this.pending) { all.set(p, o); o += p.length; }
      this.pending = []; this.pendingLen = 0;
      const pcm = toPcm16(all, rate);
      this.onChunk(b64(new Uint8Array(pcm.buffer)));
      this.onLevel(this.peak);
      this.peak = 0;
    }
  }

  /** A mic context iOS suspended (call, Siri, another app) comes back on the next tap. */
  private watch(ctx: AudioContext): void {
    if (this.ctx !== ctx) return;
    const stuck = ctx.state === "suspended" || ctx.state === "interrupted";
    if (stuck && !this.disarm) this.disarm = resumeOnNextTap(ctx, () => { this.disarm = null; });
    if (!stuck && this.disarm) { this.disarm(); this.disarm = null; }
  }
}
