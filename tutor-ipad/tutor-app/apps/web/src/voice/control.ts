// The voice state machine behind the Talk button: off → starting (mic permission) → loading (the Mac loads the speech
// models, ~4 s the first time) → listening ⇄ speaking. Owns the Mic and the Player; App feeds it server messages.
// toggle() must be called synchronously from the tap: both AudioContexts are created inside it (iOS Safari).
import type { Anchor, ClientMessage, ServerMessage } from "../protocol";
import { describeError, micErrorMessage, type Notice } from "../ui/wording";
import { Mic, Player, TARGET_RATE } from "./voice";

export type VoicePhase = "off" | "starting" | "loading" | "listening" | "speaking";

export interface VoiceView {
  phase: VoicePhase;
  /** why voice is off or what it is waiting for, in words for the student */
  note: string | null;
  /** the browser holds the tutor's sound back until the next tap (iOS suspended / interrupted) */
  blocked: boolean;
  /** what the speech recognizer hears right now (partial transcript) */
  heard: string | null;
  /** set when this page can't use the mic at all (not HTTPS): the button still shows, with this reason */
  unavailable: string | null;
}

export const LOADING_NOTE = "Loading voice… the first time takes a few seconds while the Mac loads the speech models.";
export const NO_VOICE_SERVER = "The server on the Mac is running without its models, so it can't listen or talk. Start it normally (make dev) and tap Talk again.";
export const LOAD_TIMEOUT_MS = 60_000;

export interface VoiceDeps {
  send: (m: ClientMessage) => void;
  /** what the pen is pointing at: "this" in "why is this wrong?" */
  pen: () => Anchor | null;
  notify: (n: Notice) => void;
}

export class VoiceControl {
  readonly player = new Player();
  private view: VoiceView;
  private listeners = new Set<() => void>();
  private levels = new Set<(rms: number) => void>();
  private mic: Mic | null = null;
  private gen = 0; // bumps on every start/stop, so a late mic grant after "off" is dropped
  private sessionMsgs = 0; // `session` messages since the socket opened: the first one is the hello, not a reply
  private timer: ReturnType<typeof setTimeout> | null = null;

  private deps: VoiceDeps = { send: () => {}, pen: () => null, notify: () => {} };

  constructor(deps?: VoiceDeps) {
    if (deps) this.deps = deps;
    this.view = { phase: "off", note: null, blocked: false, heard: null, unavailable: Mic.available() };
    this.player.onPlaying = (on) => {
      const p = this.view.phase;
      if (p === "listening" || p === "speaking") this.set({ phase: on ? "speaking" : "listening" });
    };
    this.player.onBlocked = (blocked) => this.set({ blocked });
  }

  // useSyncExternalStore
  subscribe = (fn: () => void): (() => void) => { this.listeners.add(fn); return () => { this.listeners.delete(fn); }; };
  getView = (): VoiceView => this.view;
  /** Mic level (RMS, ~10 per second) for the meter; only the meter re-renders. */
  subscribeLevel = (fn: (rms: number) => void): (() => void) => { this.levels.add(fn); return () => { this.levels.delete(fn); }; };

  /** The Talk button. Call it straight from the click handler (no await before it). */
  toggle = (): void => { if (this.view.phase === "off") this.start(); else this.stop(); };

  /** The Stop button while the tutor talks (the same as barging in, without having to speak). */
  stopSpeaking = (): void => { this.player.stop(); this.deps.send({ type: "voice.stop" }); };

  clearNote = (): void => this.set({ note: null });

  start(): void {
    const why = Mic.available();
    if (why) {
      this.set({ note: why, unavailable: why });
      this.deps.notify({ kind: "warn", title: "Voice needs HTTPS", text: why, key: "voice" });
      return;
    }
    this.player.unlock(); // iOS: the speaker only starts inside this tap
    const gen = ++this.gen;
    const mic = new Mic(
      (pcm) => this.deps.send({ type: "voice.audio", pcm16_b64: pcm, sample_rate: TARGET_RATE, pen: this.deps.pen() }),
      () => { if (this.player.playing) this.stopSpeaking(); }, // barge-in
      (rms) => this.levels.forEach((f) => f(rms)),
    );
    this.mic = mic;
    this.set({ phase: "starting", note: null, heard: null });
    mic.start().then(() => {
      if (gen !== this.gen) return;
      this.deps.send({ type: "session.update", voice: true });
      this.set({ phase: "loading", note: LOADING_NOTE });
      this.timer = setTimeout(() => {
        if (gen !== this.gen || this.view.phase !== "loading") return;
        const text = "The Mac didn't confirm voice within a minute. Tap Talk to try again.";
        this.stop(); this.set({ note: text });
        this.deps.notify({ kind: "warn", title: "Voice didn't start", text, key: "voice" });
      }, LOAD_TIMEOUT_MS);
    }, (e: unknown) => {
      if (gen !== this.gen) return;
      this.mic = null;
      const text = micErrorMessage(e);
      this.set({ phase: "off", note: text });
      this.deps.notify({ kind: "warn", title: "Microphone unavailable", text, key: "voice" });
    });
  }

  /** Turn voice off; `tell` = let the server know (not when the server is the one that said no). */
  stop(tell = true): void {
    const was = this.view.phase;
    this.gen++;
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    this.set({ phase: "off", heard: null, note: null }); // first, so the player stopping can't flip it back to listening
    this.mic?.stop(); this.mic = null;
    this.player.stop();
    if (tell && was !== "off" && was !== "starting") this.deps.send({ type: "session.update", voice: false });
  }

  /** Wire it to the session's socket and page (App does it in the effect that owns them). */
  attach(deps: VoiceDeps): void { this.deps = deps; }

  /** The socket (re)opened. */
  connected(): void { this.sessionMsgs = 0; }

  /** Every server message passes here first; returns true when it was audio (not state for the reducer). */
  onServer(msg: ServerMessage): boolean {
    switch (msg.type) {
      case "voice.audio":
        if (this.view.phase !== "off") this.player.play(msg.pcm16_b64, msg.sample_rate);
        return true;
      case "voice.transcript":
        this.set({ heard: msg.final ? null : msg.text });
        return false;
      case "status": {
        const d = msg.detail ?? "";
        if (msg.phase === "error" && /^voice:/i.test(d)) {
          this.stop(false); // the server couldn't load voice; the reducer toasts it
          this.set({ note: describeError(d).text ?? d });
        } else if (this.view.phase === "loading" && /^voice on$/i.test(d)) this.ready();
        else if (this.view.phase === "loading" && /loading voice/i.test(d)) this.set({ note: LOADING_NOTE });
        return false;
      }
      case "session":
        this.sessionMsgs++;
        if (this.view.phase === "loading") {
          if (msg.session.voice) this.ready();
          else if (this.sessionMsgs > 1) { // the reply to our session.update: the server didn't turn voice on
            this.stop(false);
            this.set({ note: NO_VOICE_SERVER });
            this.deps.notify({ kind: "warn", title: "Voice isn't available", text: NO_VOICE_SERVER, key: "voice" });
          }
        }
        return false;
      default:
        return false;
    }
  }

  /** Unmount: release the mic and both audio contexts (a later start() makes new ones). */
  dispose(): void {
    this.gen++;
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    this.set({ phase: "off", heard: null });
    this.mic?.stop(); this.mic = null;
    this.player.dispose();
  }

  private ready(): void {
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    this.set({ phase: this.player.playing ? "speaking" : "listening", note: null });
  }

  private set(p: Partial<VoiceView>): void {
    const next = { ...this.view, ...p };
    if ((Object.keys(p) as (keyof VoiceView)[]).every((k) => next[k] === this.view[k])) return;
    this.view = next;
    this.listeners.forEach((f) => f());
  }
}

/** The line under the pen's last position (with a 30 px margin), as the voice anchor. */
export function penAnchor(pt: { x: number; y: number } | null | undefined, lines: { id: string; bbox: { x: number; y: number; w: number; h: number } }[]): Anchor | null {
  if (!pt) return null;
  const near = lines.find((l) => pt.x >= l.bbox.x - 30 && pt.x <= l.bbox.x + l.bbox.w + 30 && pt.y >= l.bbox.y - 30 && pt.y <= l.bbox.y + l.bbox.h + 30);
  return near ? { line_id: near.id } : null;
}
