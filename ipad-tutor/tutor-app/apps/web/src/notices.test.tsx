// Phase 10: memory light, error toasts, reconnect banner + backoff, onboarding.
import { act, type ReactElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { RuntimeStatus, ServerMessage } from "./protocol";
import { backoffMs, Connection, initialState, reduce, type Action, type TutorState } from "./session";
import { ConnectionBanner, MemoryIndicator, Toasts } from "./ui/Notices";
import { markOnboarded, Onboarding, ONBOARDED_KEY, shouldShowOnboarding, STEPS } from "./ui/Onboarding";
import { describeError, memoryLight, micErrorMessage } from "./ui/wording";

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const rs = (o: Partial<RuntimeStatus> = {}): RuntimeStatus => ({
  available_gb: 12.3, level: "ok", tier2_model: "qwen36-35b-a3b", tier2_mode: "full", detail: "full", event: "", event_id: 0, ...o,
});
const server = (msg: ServerMessage): Action => ({ kind: "server", msg });
const run = (actions: Action[], s: TutorState = initialState) => actions.reduce(reduce, s);

let root: Root | null = null;
let host: HTMLDivElement | null = null;
function render(el: ReactElement): HTMLDivElement {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  act(() => root!.render(el));
  return host;
}
afterEach(() => {
  act(() => root?.unmount());
  host?.remove();
  root = null; host = null;
});

describe("memory light", () => {
  it("is green when the tutor is resident and memory is fine", () => {
    expect(memoryLight(rs()).light).toBe("green");
    expect(memoryLight(rs()).label).toBe("12 GB");
  });
  it("is amber in offload mode, and says why after a downgrade", () => {
    expect(memoryLight(rs({ tier2_mode: "offload", detail: "offload (9 GB expert cache)" }))).toMatchObject({ light: "amber" });
    const down = memoryLight(rs({ tier2_mode: "offload", event: "Memory low: tutor switched to slower mode (1.8 GiB free)", event_id: 1 }));
    expect(down.text).toContain("Memory low: tutor switched to slower mode");
    expect(memoryLight(rs({ level: "low", available_gb: 3.2 })).light).toBe("amber");
  });
  it("is red when memory is critical or the model was refused", () => {
    expect(memoryLight(rs({ level: "critical", available_gb: 1.4 })).light).toBe("red");
    const refused = memoryLight(rs({ tier2_mode: "refused", detail: "not enough free memory" }));
    expect(refused.light).toBe("red");
    expect(refused.text).toMatch(/Not enough memory/);
    expect(memoryLight(null).light).toBe("grey");
  });
  it("renders as a compact toolbar button that opens Settings", () => {
    const onClick = vi.fn();
    const el = render(<MemoryIndicator status={rs({ level: "critical", available_gb: 1.4 })} onClick={onClick} />);
    const b = el.querySelector<HTMLButtonElement>("[data-testid=mem-light]")!;
    expect(b.className).toContain("red");
    expect(b.textContent).toBe("1 GB");
    expect(b.title).toMatch(/Memory very low/);
    act(() => b.click());
    expect(onClick).toHaveBeenCalled();
  });
});

describe("runtime messages → toasts", () => {
  it("toasts a downgrade event once, even if the status repeats", () => {
    const ev = rs({ tier2_mode: "offload", event: "Memory low: tutor switched to slower mode (1.8 GiB free)", event_id: 1 });
    const s = run([server({ type: "runtime", status: ev }), server({ type: "runtime", status: ev })]);
    expect(s.runtime?.tier2_mode).toBe("offload");
    expect(s.toasts).toHaveLength(1);
    expect(s.toasts[0]!.title).toMatch(/slower mode/);
  });
  it("toasts a refused model and critical memory on the transition only", () => {
    const refused = rs({ tier2_mode: "refused", detail: "not enough free memory for qwen36" });
    let s = run([server({ type: "runtime", status: rs() }), server({ type: "runtime", status: refused }), server({ type: "runtime", status: refused })]);
    expect(s.toasts.map((t) => t.key)).toEqual(["refused"]);
    s = run([server({ type: "runtime", status: rs({ level: "critical", available_gb: 1.2 }) })], s);
    expect(s.toasts.map((t) => t.key)).toEqual(["refused", "memory-critical"]);
  });
});

describe("error states", () => {
  it("status errors become toasts with words a student understands", () => {
    const s = run([
      server({ type: "status", phase: "error", detail: "tutor: HTTP 500" }),
      server({ type: "status", phase: "error", detail: "voice: not enough memory for voice now (need 3.0 GiB)" }),
      server({ type: "status", phase: "error", detail: "anthropic unavailable — answering locally" }),
    ]);
    expect(s.toasts.map((t) => t.title)).toEqual(["The tutor had a problem", "Voice couldn't start", "Cloud tutor unavailable"]);
    expect(s.toasts[0]!.text).toContain("your ink is safe");
    expect(s.error).toBe("anthropic unavailable — answering locally"); // the panel still shows the last error
    expect(describeError("voice: voice models are not downloaded").voice).toBe(true);
  });
  it("dedupes by key and keeps at most three", () => {
    let s = run([server({ type: "status", phase: "error", detail: "tutor: a" }), server({ type: "status", phase: "error", detail: "tutor: b" })]);
    expect(s.toasts).toHaveLength(1);
    expect(s.toasts[0]!.text).toContain("b");
    for (const k of ["x", "y", "z"]) s = reduce(s, { kind: "toast", notice: { kind: "info", title: k, key: k } });
    expect(s.toasts.map((t) => t.title)).toEqual(["x", "y", "z"]);
    s = reduce(s, { kind: "dismiss-toast", id: s.toasts[0]!.id });
    expect(s.toasts.map((t) => t.title)).toEqual(["y", "z"]);
  });
  it("explains mic permission and HTTPS", () => {
    expect(micErrorMessage({ name: "NotAllowedError", message: "denied" })).toMatch(/permission was denied.*HTTPS/);
    expect(micErrorMessage({ name: "NotFoundError" })).toMatch(/No microphone/);
  });
  it("toasts auto-dismiss, and × dismisses at once", () => {
    vi.useFakeTimers();
    try {
      const onDismiss = vi.fn();
      const el = render(<Toasts toasts={[
        { id: 1, kind: "info", title: "Reconnected", key: "conn" },
        { id: 2, kind: "error", title: "The tutor had a problem", text: "HTTP 500", key: "tutor" },
      ]} onDismiss={onDismiss} />);
      expect(el.querySelectorAll("[data-testid=toast]")).toHaveLength(2);
      expect(el.querySelector(".toast.error")!.getAttribute("role")).toBe("alert");
      act(() => el.querySelectorAll<HTMLButtonElement>(".toast-x")[1]!.click());
      expect(onDismiss).toHaveBeenCalledWith(2);
      act(() => { vi.advanceTimersByTime(4000); });
      expect(onDismiss).toHaveBeenCalledWith(1); // info goes after a few seconds: never covers the page for long
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("reconnect", () => {
  it("tracks outages and toasts on reconnect", () => {
    let s = run([{ kind: "connected", value: true }]);
    expect(s.toasts).toHaveLength(0); // the first connect is silent
    s = run([{ kind: "connected", value: false, attempt: 1, retryInMs: 320 }], s);
    expect(s.conn).toMatchObject({ everConnected: true, attempt: 1, retryInMs: 320, outage: 1 });
    s = run([{ kind: "connected", value: false, attempt: 2, retryInMs: 640 }, { kind: "connected", value: true }], s);
    expect(s.conn.outage).toBe(1);
    expect(s.toasts.map((t) => t.title)).toEqual(["Reconnected"]);
  });

  it("shows a small banner with Retry now while disconnected", () => {
    const onRetry = vi.fn();
    const conn = { everConnected: true, attempt: 3, retryInMs: 2400, outage: 1 };
    const el = render(<ConnectionBanner connected={false} conn={conn} onRetry={onRetry} />);
    const banner = el.querySelector("[data-testid=conn-banner]")!;
    expect(banner.textContent).toMatch(/lost — reconnecting in 2 s \(attempt 3\)/);
    act(() => banner.querySelector("button")!.click());
    expect(onRetry).toHaveBeenCalled();
    act(() => banner.querySelector<HTMLButtonElement>(".toast-x")!.click());
    expect(el.querySelector("[data-testid=conn-banner]")).toBeNull(); // dismissable
  });

  it("backs off exponentially with jitter, capped", () => {
    expect(backoffMs(0, () => 0)).toBe(300);
    expect(backoffMs(3, () => 0)).toBe(2400);
    expect(backoffMs(20, () => 0)).toBe(8000);
    expect(backoffMs(20, () => 1)).toBe(10000);
  });

  it("Connection reconnects after close, reports attempts, flushes the queue, and Retry now skips the wait", () => {
    vi.useFakeTimers();
    try {
      const sockets: FakeSocket[] = [];
      const states: [boolean, unknown][] = [];
      const c = new Connection("ws://x", () => {}, (v, info) => states.push([v, info]), () => {
        const s = new FakeSocket(); sockets.push(s); return s as unknown as WebSocket;
      });
      sockets[0]!.open();
      c.send({ type: "voice.stop" });
      expect(sockets[0]!.sent).toHaveLength(1);
      sockets[0]!.drop();
      expect(states.at(-1)![0]).toBe(false);
      expect((states.at(-1)![1] as { attempt: number }).attempt).toBe(1);
      c.send({ type: "voice.stop" }); // queued while down
      vi.advanceTimersByTime(400);
      expect(sockets).toHaveLength(2);
      sockets[1]!.drop();
      c.retryNow(); // don't wait for the backoff
      expect(sockets).toHaveLength(3);
      sockets[2]!.open();
      expect(sockets[2]!.sent).toHaveLength(1);
      expect(states.at(-1)![0]).toBe(true);
      vi.advanceTimersByTime(20000);
      expect(sockets).toHaveLength(3); // the cancelled timer didn't open a 4th socket
      c.close();
    } finally {
      vi.useRealTimers();
    }
  });
});

class FakeSocket {
  readyState = 0;
  sent: string[] = [];
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onmessage: ((e: { data: string }) => void) | null = null;
  open() { this.readyState = 1; this.onopen?.(); }
  drop() { this.readyState = 3; this.onclose?.(); }
  send(d: string) { this.sent.push(d); }
  close() { this.drop(); }
}

describe("onboarding", () => {
  beforeEach(() => localStorage.clear());

  it("is shown once, then remembered", () => {
    expect(shouldShowOnboarding()).toBe(true);
    markOnboarded();
    expect(localStorage.getItem(ONBOARDED_KEY)).toBe("1");
    expect(shouldShowOnboarding()).toBe(false);
  });

  it("still works when storage throws (private mode)", () => {
    const get = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("blocked"); });
    const set = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("blocked"); });
    try {
      expect(shouldShowOnboarding()).toBe(true);
      expect(() => markOnboarded()).not.toThrow();
    } finally {
      get.mockRestore(); set.mockRestore();
    }
  });

  it("walks through 3–4 short steps and closes on Start or Skip", () => {
    expect(STEPS.length).toBeGreaterThanOrEqual(3);
    expect(STEPS.length).toBeLessThanOrEqual(4);
    const onClose = vi.fn();
    const el = render(<Onboarding onClose={onClose} />);
    const title = () => el.querySelector("h2")!.textContent;
    const button = (label: string) => [...el.querySelectorAll("button")].find((b) => b.textContent === label);
    expect(title()).toBe("Write with the Pencil");
    act(() => button("Next")!.click());
    expect(title()).toMatch(/marks/);
    act(() => button("Back")!.click());
    expect(title()).toBe("Write with the Pencil");
    act(() => button("Next")!.click());
    act(() => button("Next")!.click());
    expect(title()).toBe("Talk to it");
    expect(el.textContent).toMatch(/HTTPS/); // the voice step says the mic needs HTTPS on the iPad
    for (let i = 3; i < STEPS.length; i++) act(() => button("Next")!.click());
    expect(button("Next")).toBeUndefined();
    act(() => button("Start")!.click());
    expect(onClose).toHaveBeenCalledTimes(1);
    act(() => button("Skip")!.click());
    expect(onClose).toHaveBeenCalledTimes(2);
  });
});
