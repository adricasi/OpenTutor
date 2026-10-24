// The Talk button: states, the iOS audio unlock inside the tap, the level meter, server replies, the HTTPS message.
import { act, useSyncExternalStore } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ClientMessage, ServerMessage, Session } from "./protocol";
import { MoreMenu } from "./ui/MoreMenu";
import { TalkButton, VoiceDock } from "./ui/VoiceDock";
import { LOADING_NOTE, NO_VOICE_SERVER, VoiceControl } from "./voice/control";
import { levelFraction, Player } from "./voice/voice";

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

// ---------------------------------------------------------------- fake Web Audio + getUserMedia
let inClick = false; // true while a click event is being dispatched (the user gesture)
let allowResume = true; // false = the browser refuses resume() (iOS outside a gesture)
const markClick = () => { inClick = true; };
const unmarkClick = () => { inClick = false; };

class FakeSource {
  buffer: unknown = null;
  onended: (() => void) | null = null;
  connect() {}
  start = vi.fn();
  stop = vi.fn(() => { this.onended?.(); });
}
class FakeCtx {
  static made: FakeCtx[] = [];
  state: AudioContextState;
  sampleRate = 48000;
  currentTime = 0;
  destination = {};
  onstatechange: (() => void) | null = null;
  createdInClick = inClick;
  resumedInClick = false;
  resumes = 0;
  sources: FakeSource[] = [];
  audioWorklet = { addModule: vi.fn(async () => {}) };
  constructor() {
    this.state = "suspended"; // what iOS gives you
    FakeCtx.made.push(this);
  }
  resume(): Promise<void> {
    this.resumes++;
    if (inClick) this.resumedInClick = true;
    if (allowResume && this.state !== "closed") { this.state = "running"; this.onstatechange?.(); }
    return allowResume ? Promise.resolve() : Promise.reject(new Error("not allowed"));
  }
  close(): Promise<void> { this.state = "closed"; return Promise.resolve(); }
  createBuffer(_ch: number, len: number, rate: number) { return { duration: len / rate, getChannelData: () => new Float32Array(len) }; }
  createBufferSource() { const s = new FakeSource(); this.sources.push(s); return s; }
  createMediaStreamSource() { return { connect() {} }; }
}
class FakeNode {
  static last: FakeNode | null = null;
  port: { onmessage: ((e: { data: Float32Array }) => void) | null } = { onmessage: null };
  constructor() { FakeNode.last = this; }
}
const track = { stop: vi.fn() };
const getUserMedia = vi.fn(async () => ({ getTracks: () => [track] }));

function setSecure(v: boolean) { Object.defineProperty(window, "isSecureContext", { value: v, configurable: true }); }

beforeEach(() => {
  FakeCtx.made = []; FakeNode.last = null; allowResume = true; inClick = false;
  track.stop.mockClear(); getUserMedia.mockClear();
  getUserMedia.mockImplementation(async () => ({ getTracks: () => [track] }));
  vi.stubGlobal("AudioContext", FakeCtx);
  vi.stubGlobal("AudioWorkletNode", FakeNode);
  Object.defineProperty(navigator, "mediaDevices", { value: { getUserMedia }, configurable: true });
  URL.createObjectURL = vi.fn(() => "blob:tap");
  setSecure(true);
  document.addEventListener("click", markClick, true); // before React's root listener
  window.addEventListener("click", unmarkClick); // after it
});

let root: Root | null = null;
let host: HTMLDivElement | null = null;
afterEach(() => {
  act(() => root?.unmount());
  host?.remove();
  root = null; host = null;
  document.removeEventListener("click", markClick, true);
  window.removeEventListener("click", unmarkClick);
  vi.unstubAllGlobals();
});

// ---------------------------------------------------------------- harness
function Harness({ vc, transcripts = [] }: { vc: VoiceControl; transcripts?: string[] }) {
  const view = useSyncExternalStore(vc.subscribe, vc.getView);
  return (<><TalkButton view={view} onClick={vc.toggle} /><VoiceDock view={view} control={vc} transcripts={transcripts} tutorSaid={null} /></>);
}
function setup(transcripts: string[] = []) {
  const sent: ClientMessage[] = [];
  const notify = vi.fn();
  const vc = new VoiceControl({ send: (m) => sent.push(m), pen: () => ({ line_id: "l1" }), notify });
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  act(() => root!.render(<Harness vc={vc} transcripts={transcripts} />));
  const q = (sel: string) => host!.querySelector<HTMLElement>(sel);
  const fab = () => q("[data-testid=talk]")! as HTMLButtonElement;
  return { vc, sent, notify, q, fab, server: (m: ServerMessage) => act(() => { vc.onServer(m); }) };
}
const flush = () => act(async () => { for (let i = 0; i < 5; i++) await new Promise((r) => setTimeout(r, 0)); });
const session = (voice: boolean): ServerMessage => ({ type: "session", session: { id: "s", mode: "math", problem: "", provider: "local", voice, pages: [] } as unknown as Session, tier2: null } as unknown as ServerMessage);
const tone = (n: number, amp: number) => Float32Array.from({ length: n }, (_, i) => amp * Math.sin(i / 3));
const pcm = (n: number) => btoa(String.fromCharCode(...new Uint8Array(n * 2)));

async function listening(t: ReturnType<typeof setup>) {
  act(() => t.fab().click());
  await flush();
  t.server({ type: "status", phase: "recognizing", detail: "loading voice…" });
  t.server({ type: "status", phase: "waiting", detail: "voice on" });
}

// ---------------------------------------------------------------- tests
describe("Talk button", () => {
  it("is always rendered with a clear label, off by default", () => {
    const t = setup();
    expect(t.fab().textContent).toBe("Talk");
    expect(t.fab().getAttribute("aria-pressed")).toBe("false");
    expect(t.q("[data-testid=voice]")!.textContent).toBe("Talk"); // the compact toolbar entry
    expect(t.q("[data-testid=voice-bubble]")).toBeNull();
  });

  it("creates and resumes both AudioContexts inside the tap (iOS unlock), then plays a silent sample", () => {
    const t = setup();
    act(() => t.fab().click());
    expect(FakeCtx.made).toHaveLength(2); // speaker (Player.unlock) + mic, both before any await
    for (const c of FakeCtx.made) {
      expect(c.createdInClick).toBe(true);
      expect(c.resumedInClick).toBe(true);
      expect(c.state).toBe("running");
    }
    expect(FakeCtx.made[0]!.sources[0]!.start).toHaveBeenCalledWith(0); // the 1-sample unlock
    expect(getUserMedia).toHaveBeenCalledTimes(1);
    expect(t.fab().textContent).toBe("Allow the mic…");
  });

  it("goes starting → loading (with a wait message) → listening, and streams PCM16 with the pen anchor", async () => {
    const t = setup();
    act(() => t.fab().click());
    await flush();
    expect(t.sent).toContainEqual({ type: "session.update", voice: true });
    expect(t.fab().textContent).toBe("Loading voice…");
    expect(t.q("[data-testid=voice-note]")!.textContent).toBe(LOADING_NOTE);
    t.server({ type: "status", phase: "recognizing", detail: "loading voice…" });
    expect(t.fab().textContent).toBe("Loading voice…");
    t.server({ type: "status", phase: "waiting", detail: "voice on" });
    expect(t.fab().textContent).toBe("Listening…");
    expect(t.q("[data-testid=voice]")!.textContent).toBe("Listening");
    expect(t.q("[data-testid=voice-note]")).toBeNull();

    act(() => FakeNode.last!.port.onmessage!({ data: tone(4800, 0.3) })); // 100 ms at 48 kHz
    const audio = t.sent.filter((m) => m.type === "voice.audio");
    expect(audio).toHaveLength(1);
    expect(audio[0]).toMatchObject({ sample_rate: 16000, pen: { line_id: "l1" } });
    expect(atob((audio[0] as { pcm16_b64: string }).pcm16_b64).length).toBe(1600 * 2);
  });

  it("shows a live level meter from the mic RMS", async () => {
    const t = setup();
    await listening(t);
    const meter = () => t.q("[data-testid=level-meter]")!;
    expect(meter().getAttribute("aria-valuenow")).toBe("0");
    act(() => FakeNode.last!.port.onmessage!({ data: tone(4800, 0.3) }));
    expect(Number(meter().getAttribute("aria-valuenow"))).toBeGreaterThan(70);
    expect(meter().querySelectorAll("span.on").length).toBeGreaterThanOrEqual(4);
    act(() => FakeNode.last!.port.onmessage!({ data: tone(4800, 0.0005) })); // near silence
    expect(Number(meter().getAttribute("aria-valuenow"))).toBeLessThan(15);
  });

  it("Sound off: the tutor's voice isn't played (its words still show) and what was playing stops", async () => {
    const t = setup();
    await listening(t);
    t.server({ type: "voice.audio", pcm16_b64: pcm(2400), sample_rate: 24000 });
    expect(t.fab().textContent).toBe("Tutor speaking");
    act(() => t.q("[data-testid=voice-mute]")!.click());
    expect(t.sent.at(-1)).toEqual({ type: "voice.stop" });
    expect(t.fab().textContent).toBe("Listening…");
    expect(window.localStorage.getItem("inktutor.muted.v1")).toBe("1");
    t.server({ type: "voice.audio", pcm16_b64: pcm(2400), sample_rate: 24000 });
    expect(t.fab().textContent).toBe("Listening…"); // nothing played
    act(() => t.q("[data-testid=voice-mute]")!.click());
    t.server({ type: "voice.audio", pcm16_b64: pcm(2400), sample_rate: 24000 });
    expect(t.fab().textContent).toBe("Tutor speaking");
  });

  it("shows 'Tutor speaking' with a Stop button that stops playback and tells the server", async () => {
    const t = setup();
    await listening(t);
    t.server({ type: "voice.audio", pcm16_b64: pcm(2400), sample_rate: 24000 });
    expect(t.fab().textContent).toBe("Tutor speaking");
    const stop = t.q("[data-testid=voice-stop]") as HTMLButtonElement;
    expect(stop).not.toBeNull();
    act(() => stop.click());
    expect(t.sent.at(-1)).toEqual({ type: "voice.stop" });
    expect(t.fab().textContent).toBe("Listening…");
    expect(t.q("[data-testid=voice-stop]")).toBeNull();
  });

  it("shows the last transcript lines and the partial being heard next to the button", async () => {
    const t = setup(["one", "why is this wrong", "just a hint"]);
    await listening(t);
    t.server({ type: "voice.transcript", text: "show me the", final: false });
    const text = t.q("[data-testid=voice-bubble]")!.textContent!;
    expect(text).toContain("“why is this wrong”");
    expect(text).toContain("“just a hint”");
    expect(text).not.toContain("“one”");
    expect(text).toContain("show me the…");
  });

  it("the words can be hidden (status stays) and the choice is remembered on this device", async () => {
    const t = setup(["why is this wrong"]);
    await listening(t);
    t.server({ type: "voice.transcript", text: "show me", final: false });
    const bubble = () => t.q("[data-testid=voice-bubble]")!.textContent!;
    expect(bubble()).toContain("“why is this wrong”");
    act(() => t.q("[data-testid=voice-words]")!.click());
    expect(bubble()).not.toContain("why is this wrong");
    expect(bubble()).not.toContain("show me");
    expect(bubble()).toContain("Listening");
    expect(window.localStorage.getItem("inktutor.showTranscript.v1")).toBe("0");
    act(() => t.q("[data-testid=voice-words]")!.click());
    expect(bubble()).toContain("“why is this wrong”");
  });

  it("turning off stops the mic and tells the server", async () => {
    const t = setup();
    await listening(t);
    act(() => t.fab().click());
    expect(t.sent.at(-1)).toEqual({ type: "session.update", voice: false });
    expect(track.stop).toHaveBeenCalled();
    expect(t.fab().textContent).toBe("Talk");
  });

  it("a server error (models missing) is shown and the button returns to off", async () => {
    const t = setup();
    act(() => t.fab().click());
    await flush();
    t.server({ type: "status", phase: "error", detail: "voice: voice models are not downloaded: make models" });
    expect(t.fab().textContent).toBe("Talk");
    expect(t.q("[data-testid=voice-note]")!.textContent).toMatch(/not downloaded.*make models/);
    expect(track.stop).toHaveBeenCalled();
  });

  it("a server without models answers voice:false: that is an error, but the hello on connect is not", async () => {
    const t = setup();
    t.vc.connected();
    act(() => t.fab().click());
    await flush();
    t.server(session(false)); // hello on connect (the socket opened after the tap)
    expect(t.fab().textContent).toBe("Loading voice…");
    t.server(session(false)); // the reply to session.update
    expect(t.fab().textContent).toBe("Talk");
    expect(t.q("[data-testid=voice-note]")!.textContent).toContain(NO_VOICE_SERVER);
    expect(t.notify).toHaveBeenCalledWith(expect.objectContaining({ title: "Voice isn't available" }));
  });

  it("mic permission denied → a clear message, off", async () => {
    getUserMedia.mockImplementation(async () => { throw Object.assign(new Error("denied"), { name: "NotAllowedError" }); });
    const t = setup();
    act(() => t.fab().click());
    await flush();
    expect(t.fab().textContent).toBe("Talk");
    expect(t.q("[data-testid=voice-note]")!.textContent).toMatch(/permission was denied/);
    expect(FakeCtx.made.at(-1)!.state).toBe("closed"); // the mic context is released
  });

  it("without HTTPS the button still shows, says why, and doesn't touch the mic", () => {
    setSecure(false);
    const t = setup();
    expect(t.fab().textContent).toBe("Talkneeds HTTPS");
    act(() => t.fab().click());
    expect(t.q("[data-testid=voice-note]")!.textContent).toMatch(/secure page \(HTTPS\)/);
    expect(t.notify).toHaveBeenCalledWith(expect.objectContaining({ title: "Voice needs HTTPS" }));
    expect(getUserMedia).not.toHaveBeenCalled();
    expect(FakeCtx.made).toHaveLength(0);
    expect(t.fab().textContent).toContain("Talk");
  });
});

describe("Player on iOS", () => {
  it("a suspended or interrupted context is resumed on the next tap, and says it's blocked meanwhile", async () => {
    allowResume = false; // audio arrives outside a gesture
    const p = new Player();
    const blocked: boolean[] = [];
    p.onBlocked = (b) => blocked.push(b);
    p.play(pcm(240), 24000);
    expect(p.blocked).toBe(true);
    expect(blocked).toEqual([true]);
    const ctx = FakeCtx.made[0]!;
    allowResume = true;
    document.dispatchEvent(new Event("pointerup")); // the next pen or finger lift anywhere
    await Promise.resolve();
    expect(ctx.state).toBe("running");
    expect(blocked).toEqual([true, false]);
    // iOS: a call / Siri interrupts it later
    allowResume = false;
    ctx.state = "interrupted";
    act(() => ctx.onstatechange!());
    expect(p.blocked).toBe(true);
    allowResume = true;
    document.dispatchEvent(new Event("touchend"));
    await Promise.resolve();
    expect(p.blocked).toBe(false);
    p.dispose();
  });

  it("levelFraction maps RMS on a log scale to 0..1", () => {
    expect(levelFraction(0)).toBe(0);
    expect(levelFraction(0.001)).toBe(0); // −60 dBFS
    expect(levelFraction(0.316)).toBeCloseTo(1, 1); // −10 dBFS
    expect(levelFraction(1)).toBe(1);
    expect(levelFraction(0.03)).toBeGreaterThan(0.3);
  });
});

describe("toolbar More menu", () => {
  it("opens, closes on an item tap and on a tap outside", () => {
    host = document.createElement("div");
    document.body.appendChild(host);
    root = createRoot(host);
    const onItem = vi.fn();
    act(() => root!.render(<MoreMenu><button onClick={onItem}>Fingers</button></MoreMenu>));
    const more = host.querySelector<HTMLButtonElement>("[data-testid=more]")!;
    const wrap = () => host!.querySelector(".more")!;
    act(() => more.click());
    expect(wrap().className).toContain("open");
    expect(more.getAttribute("aria-expanded")).toBe("true");
    act(() => host!.querySelector<HTMLButtonElement>(".more-group button")!.click());
    expect(onItem).toHaveBeenCalled();
    expect(wrap().className).not.toContain("open");
    act(() => more.click());
    act(() => { document.body.dispatchEvent(new Event("pointerdown", { bubbles: true })); });
    expect(wrap().className).not.toContain("open");
  });
});
