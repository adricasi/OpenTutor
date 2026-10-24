// Session connection + state. The server is the brain; this keeps what the UI needs to draw.
import type { ClientMessage, FeedbackEvent, Reading, RuntimeStatus, ServerMessage, Session, TutorInfo } from "./protocol";
import { describeError, type Notice, type ToastKind } from "./ui/wording";

export interface Badge { provider: string; model: string; ms: number | null }
export interface Toast { id: number; kind: ToastKind; title: string; text?: string; key: string }
/** WebSocket health for the reconnect banner. `outage` counts disconnects (a new one re-shows a dismissed banner). */
export interface ConnInfo { everConnected: boolean; attempt: number; retryInMs: number | null; outage: number }

export interface TutorState {
  session: Session | null;
  tier2: TutorInfo | null;
  readings: Record<string, Reading>; // page id → latest reading
  feedback: Record<string, FeedbackEvent[]>; // page id → events in arrival order
  checking: Record<string, string[]>; // page id → line ids being checked (shimmer)
  phase: Record<string, string>;
  badge: Badge | null;
  connected: boolean;
  error: string | null;
  tier1Ms: number | null;
  transcripts: string[];
  runtime: RuntimeStatus | null; // memory watchdog (server `runtime` messages)
  seenEventId: number;
  toasts: Toast[];
  toastSeq: number;
  conn: ConnInfo;
}

export const initialState: TutorState = {
  session: null, tier2: null, readings: {}, feedback: {}, checking: {}, phase: {},
  badge: null, connected: false, error: null, tier1Ms: null, transcripts: [],
  runtime: null, seenEventId: 0, toasts: [], toastSeq: 0,
  conn: { everConnected: false, attempt: 0, retryInMs: null, outage: 0 },
};

const MAX_TOASTS = 3;

/** Add a toast; one with the same key replaces the old one (and restarts its timer). */
export function pushToast(state: TutorState, n: Notice): TutorState {
  const id = state.toastSeq + 1;
  const toasts = [...state.toasts.filter((t) => t.key !== n.key), { id, ...n }].slice(-MAX_TOASTS);
  return { ...state, toasts, toastSeq: id };
}

function onRuntime(state: TutorState, s: RuntimeStatus): TutorState {
  const prev = state.runtime;
  let next: TutorState = { ...state, runtime: s };
  const eid = s.event_id ?? 0;
  if (eid > state.seenEventId && s.event) {
    next = pushToast({ ...next, seenEventId: eid }, {
      kind: "warn", title: s.event, key: "runtime-event",
      text: "Answers will take a little longer. Close other apps to free memory; Settings shows the details.",
    });
  } else if (s.tier2_mode === "refused" && prev?.tier2_mode !== "refused") {
    next = pushToast(next, {
      kind: "error", title: "Not enough memory for the local tutor", key: "refused",
      text: `${s.detail ? s.detail + ". " : ""}Close other apps, or switch to Claude / OpenAI in Settings.`,
    });
  } else if (s.level === "critical" && prev?.level !== "critical") {
    next = pushToast(next, {
      kind: "warn", title: "Memory very low", key: "memory-critical",
      text: `${s.available_gb.toFixed(1)} GB free on the Mac. The tutor may slow down or switch to a slower mode.`,
    });
  }
  return next;
}

export type Action =
  | { kind: "server"; msg: ServerMessage }
  | { kind: "connected"; value: boolean; attempt?: number; retryInMs?: number | null }
  | { kind: "local-dismiss"; pageId: string; id: string }
  | { kind: "toast"; notice: Notice }
  | { kind: "dismiss-toast"; id: number };

export function reduce(state: TutorState, a: Action): TutorState {
  if (a.kind === "connected") {
    const c = state.conn;
    if (a.value) {
      const back = c.everConnected && !state.connected; // a reconnect, not the first connect
      const next = { ...state, connected: true, conn: { ...c, everConnected: true, attempt: 0, retryInMs: null } };
      return back ? pushToast(next, { kind: "info", title: "Reconnected", key: "conn" }) : next;
    }
    const outage = state.connected ? c.outage + 1 : c.outage;
    return { ...state, connected: false, conn: { ...c, attempt: a.attempt ?? c.attempt, retryInMs: a.retryInMs ?? null, outage } };
  }
  if (a.kind === "toast") return pushToast(state, a.notice);
  if (a.kind === "dismiss-toast") return { ...state, toasts: state.toasts.filter((t) => t.id !== a.id) };
  if (a.kind === "local-dismiss") {
    const list = state.feedback[a.pageId] ?? [];
    return { ...state, feedback: { ...state.feedback, [a.pageId]: list.filter((f) => f.id !== a.id) } };
  }
  const m = a.msg;
  switch (m.type) {
    case "session":
      return { ...state, session: m.session, tier2: m.tier2, error: null };
    case "reading":
      return {
        ...state,
        readings: { ...state.readings, [m.reading.page_id]: m.reading },
        tier1Ms: m.ms ?? null,
      };
    case "feedback": {
      const f = m.feedback;
      const list = (state.feedback[f.page_id] ?? []).filter((x) => x.id !== f.id);
      // the badge names the brain that answered: only tutor output updates it (checker marks are local and instant)
      const badge = f.source === "tutor" && m.provider ? { provider: m.provider, model: m.model ?? "", ms: m.ms ?? null } : state.badge;
      return { ...state, feedback: { ...state.feedback, [f.page_id]: [...list, f] }, badge };
    }
    case "status": {
      const pid = m.page_id ?? "";
      const checking = m.phase === "checking" || m.phase === "reviewing" || m.phase === "recognizing"
        ? (m.line_ids ?? []) : [];
      const next = {
        ...state,
        phase: { ...state.phase, [pid]: m.phase },
        checking: { ...state.checking, [pid]: checking },
        error: m.phase === "error" ? m.detail ?? "error" : state.error,
      };
      return m.phase === "error" ? pushToast(next, describeError(m.detail ?? "")) : next;
    }
    case "error":
      return pushToast({ ...state, error: m.message }, describeError(m.message));
    case "runtime":
      return onRuntime(state, m.status);
    case "voice.transcript":
      return m.final ? { ...state, transcripts: [...state.transcripts, m.text].slice(-6) } : state;
    default:
      return state;
  }
}

/** Reconnect delay: exponential from 300 ms, capped at 8 s, with up to 25 % jitter (several iPads, one Mac). */
export function backoffMs(attempt: number, rand: () => number = Math.random): number {
  const base = Math.min(8000, 300 * 2 ** attempt);
  return Math.round(base * (1 + 0.25 * rand()));
}

export interface ConnState { attempt: number; retryInMs: number | null }

/** WebSocket with automatic reconnect (backoff + jitter); messages sent while disconnected are queued.
 *  Safari drops sockets when the iPad sleeps or the tab is hidden: coming back online / visible retries at once. */
export class Connection {
  private ws: WebSocket | null = null;
  private queue: string[] = [];
  private closed = false;
  private retry = 0;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private wake = () => { if (document.visibilityState !== "hidden") this.retryNow(); };

  constructor(
    private url: string,
    private onMessage: (m: ServerMessage) => void,
    private onState: (connected: boolean, info?: ConnState) => void,
    private makeSocket: (url: string) => WebSocket = (u) => new WebSocket(u),
  ) {
    window.addEventListener("online", this.wake);
    document.addEventListener("visibilitychange", this.wake);
    this.open();
  }

  /** Skip the backoff wait (the banner's "Retry now", or the device woke up). */
  retryNow(): void {
    if (this.closed || (this.ws && this.ws.readyState <= 1)) return; // connecting or open
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    this.open();
  }

  private open(): void {
    const ws = this.makeSocket(this.url);
    this.ws = ws;
    ws.onopen = () => {
      this.retry = 0;
      this.onState(true, { attempt: 0, retryInMs: null });
      for (const q of this.queue.splice(0)) ws.send(q);
    };
    ws.onmessage = (e) => {
      try {
        this.onMessage(JSON.parse(String(e.data)) as ServerMessage);
      } catch {
        /* ignore malformed */
      }
    };
    ws.onclose = () => {
      if (this.ws !== ws) return; // an older socket closing after retryNow replaced it
      if (this.closed) { this.onState(false); return; }
      const delay = backoffMs(this.retry++);
      this.onState(false, { attempt: this.retry, retryInMs: delay });
      this.timer = setTimeout(() => { this.timer = null; if (!this.closed) this.open(); }, delay);
    };
  }

  send(m: ClientMessage): void {
    const data = JSON.stringify(m);
    if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(data);
    else this.queue.push(data);
  }

  close(): void {
    this.closed = true;
    if (this.timer) clearTimeout(this.timer);
    window.removeEventListener("online", this.wake);
    document.removeEventListener("visibilitychange", this.wake);
    this.ws?.close();
  }
}

export function wsUrl(sessionId: string): string {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${location.host}/ws/session/${sessionId}`;
}

export async function createSession(mode: string, problem: string): Promise<Session> {
  const r = await fetch("/api/sessions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ mode, problem }),
  });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return (await r.json()) as Session;
}
