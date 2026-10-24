import { useCallback, useEffect, useReducer, useRef, useState, useSyncExternalStore } from "react";
import type { ClientMessage, FeedbackEvent, InkUpdate, PageRef, Reading, Session, Stroke } from "./protocol";
import { DirtyTracker } from "./ink/dirty";
import { InkSurface } from "./ink/surface";
import { Connection, createSession, initialState, reduce, wsUrl } from "./session";
import { TutorLayer } from "./tutor/TutorLayer";
import { Popover, QuestionChips } from "./tutor/Overlays";
import { useCorrection } from "./tutor/Correction";
import { visibleFeedback, type PageIndex } from "./tutor/geometry";
import { PairButton } from "./pairing/PairingPage";
import { Settings } from "./ui/Settings";
import { ConnectionBanner, MemoryIndicator, Toasts } from "./ui/Notices";
import { markOnboarded, Onboarding, shouldShowOnboarding } from "./ui/Onboarding";
import { MoreMenu } from "./ui/MoreMenu";
import { TalkButton, VoiceDock } from "./ui/VoiceDock";
import { penAnchor, VoiceControl } from "./voice/control";

const MODES: [string, string][] = [
  ["math", "Maths"], ["consulting", "Consulting case"], ["system_design", "System design"],
  ["language", "Language"], ["accounting", "Accounting"], ["sudoku", "Sudoku"], ["other", "Other"],
];

export default function App({ initial }: { initial?: Session | null }) {
  const params = new URLSearchParams(location.search);
  const [session, setSession] = useState<Session | null>(initial ?? null);
  const [mode, setMode] = useState(params.get("mode") ?? "math");
  const [problem, setProblem] = useState(params.get("problem") ?? "");
  const [intro, setIntro] = useState(shouldShowOnboarding); // first run: a short how-it-works overlay
  const closeIntro = useCallback(() => { markOnboarded(); setIntro(false); }, []);

  const start = async () => setSession(await createSession(mode, problem));

  if (!session) {
    return (
      <div className="start">
        <form className="card" onSubmit={(e) => { e.preventDefault(); void start(); }}>
          <h1>Ink Tutor</h1>
          <label>What are you practising?
            <select value={mode} onChange={(e) => setMode(e.target.value)}>
              {MODES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select>
          </label>
          <label>The problem (what the tutor is given)
            <textarea value={problem} onChange={(e) => setProblem(e.target.value)} rows={3}
              placeholder="e.g. Compute ∫₀¹ x³ dx. Show your steps." />
          </label>
          <button type="submit">Start writing</button>
          <PairButton />
          <button type="button" className="linkish" onClick={() => setIntro(true)}>How it works</button>
        </form>
        {intro && <Onboarding onClose={closeIntro} />}
      </div>
    );
  }
  return <Workspace session={session} />;
}

function Workspace({ session }: { session: Session }) {
  const [state, dispatch] = useReducer(reduce, initialState);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const surfaceRef = useRef<InkSurface | null>(null);
  const connRef = useRef<Connection | null>(null);
  const [ink, setInk] = useState<InkSnapshot | null>(null); // what render needs from the imperative surface
  const [eraser, setEraser] = useState(false);
  const [fingers, setFingers] = useState(false);
  const [debug, setDebug] = useState(false);
  const [panel, setPanel] = useState(() => window.innerWidth >= 1400); // iPad landscape: the page gets the full width
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [popover, setPopover] = useState<{ f: FeedbackEvent; at: { x: number; y: number } } | null>(null);
  const [accepted, setAccepted] = useState<Set<string>>(new Set());
  const [photos, setPhotos] = useState<{ page: PageRef; url: string }[]>([]);
  const [photoId, setPhotoId] = useState<string | null>(null); // viewing a photo page instead of the ink page
  const [deskSize, setDeskSize] = useState({ w: 0, h: 0 });
  const [intro, setIntro] = useState(false); // re-opened from Settings
  const closeIntro = useCallback(() => setIntro(false), []);
  const dismissToast = useCallback((id: number) => dispatch({ kind: "dismiss-toast", id }), []);
  const readingRef = useRef<Reading | null>(null);
  // voice: the Talk button's state machine (mic + speaker); the click handler calls vc.toggle synchronously (iOS audio unlock)
  const [vc] = useState(() => new VoiceControl());
  const voice = useSyncExternalStore(vc.subscribe, vc.getView);
  const pageRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = pageRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setDeskSize({ w: el.clientWidth, h: el.clientHeight }));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  useEffect(() => {
    const t0 = performance.now(); // stroke times are relative to the session start
    const conn = new Connection(wsUrl(session.id), (msg) => {
      if (vc.onServer(msg)) return; // audio isn't state
      dispatch({ kind: "server", msg });
    }, (v, info) => { if (v) vc.connected(); dispatch({ kind: "connected", value: v, ...info }); });
    connRef.current = conn;
    const tracker = new DirtyTracker((m: InkUpdate) => conn.send(m), 700);
    const surface: InkSurface = new InkSurface({ canvas: canvasRef.current!, tracker, t0, onChange: () => setInk(snapshot(surface)) });
    surfaceRef.current = surface;
    setInk(snapshot(surface));
    vc.attach({
      send: (m) => conn.send(m),
      pen: () => penAnchor(surface.lastPt, readingRef.current?.lines ?? []), // "this" in "why is this wrong?"
      notify: (notice) => dispatch({ kind: "toast", notice }),
    });
    (window as unknown as { inkTutor: unknown }).inkTutor = { surface, tracker, session }; // e2e + debugging hook
    return () => { surface.dispose(); conn.close(); vc.dispose(); };
  }, [session, vc]);

  useEffect(() => { if (surfaceRef.current) surfaceRef.current.eraser = eraser; }, [eraser]);
  useEffect(() => { if (surfaceRef.current) surfaceRef.current.fingerDraws = fingers; }, [fingers]);

  const photo = photos.find((p) => p.page.id === photoId) ?? null;
  const page = photo?.page ?? ink?.page ?? null;
  const pageId = page?.id ?? "";
  // a photo is fitted to the desk; marks keep their on-screen size (TutorLayer `display`)
  const fit = photo ? Math.min(1, deskSize.w / photo.page.width, deskSize.h / photo.page.height) || 1 : 1;
  const display = photo ? { w: photo.page.width * fit, h: photo.page.height * fit } : undefined;
  const reading = state.readings[pageId] ?? null;
  const strokes = new Map<string, Stroke>((photo ? [] : ink?.strokes ?? []).map((s) => [s.id, s]));
  const index: PageIndex = { strokes, reading };
  const fb = visibleFeedback(state.feedback[pageId] ?? []);
  const pins = fb.filter((f) => f.type === "mark" && f.glyph === "pin");
  const explanations = fb.filter((f) => f.type === "explain" || f.type === "speak" || f.type === "summary");
  const tutorSaid = fb.filter((f) => f.type === "speak").at(-1)?.text ?? null;
  const graph = reading?.graph ?? null;
  const reference = fb.filter((f) => f.type === "hint" && f.hint_level === 4 && graph);
  const rootId = graph?.nodes?.length ? [...graph.nodes].sort((a, b) => a.bbox.y - b.bbox.y)[0]!.id : null;

  const send = useCallback((m: ClientMessage) => connRef.current?.send(m), []);
  // "tell the tutor what you meant": typed / dictated corrections from a "?" (chips, "?" marks)
  const fix = useCorrection(send, pageId, voice.phase !== "off", state.transcripts);
  useEffect(() => { readingRef.current = reading; }, [reading]);

  const badge = state.badge
    ? `${state.badge.provider === "local" ? "local" : state.badge.provider === "anthropic" ? "Claude" : "OpenAI"} · ${state.badge.model}${state.badge.ms != null ? ` · ${(state.badge.ms / 1000).toFixed(1)} s` : ""}`
    : state.tier2 ? `${state.tier2.provider} · ${state.tier2.model}${state.tier2.ready ? "" : " (not ready)"}` : "…";

  return (
    <div className={`app${panel ? " with-panel" : ""}`}>
      <header className="bar">
        <span className={`dot${state.connected ? " on" : ""}`} title={state.connected ? "connected" : "reconnecting"} />
        <span className="mode">{MODES.find(([v]) => v === session.mode)?.[1] ?? session.mode}</span>
        <div className="tools">
          <button className={!eraser ? "on" : ""} onClick={() => setEraser(false)}>Pen</button>
          <button className={eraser ? "on" : ""} onClick={() => setEraser(true)}>Eraser</button>
          <button onClick={() => surfaceRef.current?.undo()} disabled={!ink?.canUndo}>Undo</button>
          <button onClick={() => surfaceRef.current?.redo()} disabled={!ink?.canRedo}>Redo</button>
        </div>
        <div className="right">
          <TalkButton view={voice} onClick={vc.toggle} />
          <MemoryIndicator status={state.runtime} onClick={() => setSettingsOpen(true)} />
          <span className={`badge ${state.badge?.provider ?? state.tier2?.provider ?? "local"}`} data-testid="badge">{badge}</span>
          <MoreMenu>
            <button onClick={() => { setPhotoId(null); surfaceRef.current?.newPage(); }} title="New page">+ Page</button>
            <label className="button">Photo
              <input type="file" accept="image/*" hidden onChange={(e) => {
                const f = e.target.files?.[0];
                e.target.value = "";
                if (f) void uploadPhoto(session.id, f).then((p) => { setPhotos((ps) => [...ps, p]); setPhotoId(p.page.id); });
              }} />
            </label>
            {((ink?.pages.length ?? 0) > 1 || photos.length > 0) && (
              <select value={pageId} onChange={(e) => {
                const ph = photos.find((p) => p.page.id === e.target.value);
                setPhotoId(ph ? ph.page.id : null);
                if (!ph) surfaceRef.current?.showPage(e.target.value);
                setPopover(null);
              }}>
                {ink?.pages.map((p) => <option key={p.id} value={p.id}>Page {p.index + 1}</option>)}
                {photos.map((p, i) => <option key={p.page.id} value={p.page.id}>Photo {i + 1}</option>)}
              </select>
            )}
            <button className={fingers ? "on" : ""} onClick={() => setFingers(!fingers)} title="Let fingers draw (off = palm rejection)">Fingers</button>
            <button onClick={() => setDebug(!debug)} className={debug ? "on" : ""} title="Show what the tutor reads">Reading</button>
            <button onClick={() => setSettingsOpen(!settingsOpen)}>Settings</button>
            <button onClick={() => setPanel(!panel)} className={panel ? "on" : ""}>Panel</button>
          </MoreMenu>
        </div>
      </header>

      <main className="desk">
        <div className="page" ref={pageRef}>
          <canvas ref={canvasRef} className="ink" data-testid="ink" style={photo ? { visibility: "hidden" } : undefined} />
          {photo && display && <img className="photo" src={photo.url} alt="Your page" style={{ width: display.w, height: display.h }} />}
          {page && (
            <TutorLayer page={page} feedback={fb} index={index} checking={state.checking[pageId] ?? []}
              accepted={accepted} display={display} onOpen={(f, at) => setPopover({ f, at: { x: at.x * fit, y: at.y * fit } })} />
          )}
          <QuestionChips feedback={fb} index={index} scale={fit} onCorrect={fix.onCorrect} dictation={fix.dictation} onAnswer={(f, choice) => send({
            type: "question.answer", page_id: pageId, feedback_id: f.id, reading_line_id: f.anchor.line_id ?? "", chosen: choice,
          })} />
          {popover && page && (
            <Popover target={popover.f} at={popover.at} feedback={fb} pageWidth={display?.w ?? page.width}
              accepted={accepted.has(popover.f.id)}
              onCorrect={(text, source) => { fix.onCorrect(popover.f, text, source); setPopover(null); }} dictation={fix.dictation}
              onClose={() => setPopover(null)}
              onHint={(level, explicit) => send({
                type: "hint.request", page_id: pageId, feedback_id: popover.f.id, line_id: popover.f.anchor.line_id ?? null, level, explicit,
              })}
              onDismiss={() => {
                send({ type: "mark.dismiss", page_id: pageId, feedback_id: popover.f.id });
                dispatch({ kind: "local-dismiss", pageId, id: popover.f.id });
                setPopover(null);
              }}
              onGhost={(accept) => {
                send({ type: accept ? "ghost.accept" : "ghost.dismiss", page_id: pageId, feedback_id: popover.f.id });
                if (accept) setAccepted(new Set([...accepted, popover.f.id]));
                else dispatch({ kind: "local-dismiss", pageId, id: popover.f.id });
                setPopover(null);
              }}
            />
          )}
          {debug && <DebugPane reading={reading} phase={state.phase[pageId]} tier1Ms={state.tier1Ms} />}
          <VoiceDock view={voice} control={vc} transcripts={state.transcripts} tutorSaid={tutorSaid} />
        </div>
        {panel && (
          <aside className="panel">
            {session.problem && <section><h3>Problem</h3><p>{session.problem}</p></section>}
            {pins.length > 0 && (
              <section><h3>Checklist</h3>
                <ol>{pins.map((p) => <li key={p.id}>{p.text}</li>)}</ol>
              </section>
            )}
            <section><h3>Tutor</h3>
              {explanations.length === 0 && <p className="muted">Write; marks appear a few seconds after you lift the pen. Tap a mark for a hint.</p>}
              {explanations.map((e) => <p key={e.id} className={e.type}>{e.text}</p>)}
            </section>
            {rootId && (
              <section><h3>Reference structure</h3>
                {reference.length === 0
                  ? <button onClick={() => send({ type: "hint.request", page_id: pageId, line_id: rootId, level: 4, explicit: true })}>
                      Show a reference structure
                    </button>
                  : reference.map((r) => <pre key={r.id} className="reference">{r.text}</pre>)}
                <p className="muted">Shown here, never on your page.</p>
              </section>
            )}
            {(voice.phase !== "off" || voice.note || state.transcripts.length > 0) && (
              <section><h3>Voice {voice.phase === "listening" ? "· listening" : voice.phase === "speaking" ? "· speaking" : ""}</h3>
                {voice.note && <p className="muted">{voice.note}</p>}
                {state.transcripts.map((t, i) => <p key={i} className="said">“{t}”</p>)}
              </section>
            )}
            {state.error && <section className="err">{state.error}</section>}
          </aside>
        )}
      </main>
      {settingsOpen && <Settings session={state.session ?? session} onClose={() => setSettingsOpen(false)} send={send} tier2={state.tier2}
        runtime={state.runtime} onShowIntro={() => { setSettingsOpen(false); setIntro(true); }} />}
      <ConnectionBanner key={state.conn.outage} connected={state.connected} conn={state.conn} onRetry={() => connRef.current?.retryNow()} />
      <Toasts toasts={state.toasts} onDismiss={dismissToast} />
      {intro && <Onboarding onClose={closeIntro} />}
    </div>
  );
}

/** Downsize on the device (≤ 1600 px, JPEG) and send; the server reads it with Chandra and streams marks. */
async function uploadPhoto(sessionId: string, file: File): Promise<{ page: PageRef; url: string }> {
  const img = await createImageBitmap(file);
  const k = Math.min(1, 1600 / Math.max(img.width, img.height));
  const c = document.createElement("canvas");
  c.width = Math.round(img.width * k);
  c.height = Math.round(img.height * k);
  c.getContext("2d")!.drawImage(img, 0, 0, c.width, c.height);
  const r = await fetch(`/api/sessions/${sessionId}/photos`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ data_url: c.toDataURL("image/jpeg", 0.85) }),
  });
  if (!r.ok) throw new Error(`photo upload failed: HTTP ${r.status}`);
  return (await r.json()) as { page: PageRef; url: string };
}

interface InkSnapshot { page: PageRef; pages: PageRef[]; strokes: Stroke[]; canUndo: boolean; canRedo: boolean }

function snapshot(s: InkSurface): InkSnapshot {
  return { page: s.page, pages: s.pageList, strokes: [...s.strokes], canUndo: s.canUndo, canRedo: s.canRedo };
}

function DebugPane({ reading, phase, tier1Ms }: { reading: import("./protocol").Reading | null; phase?: string; tier1Ms: number | null }) {
  return (
    <div className="debug" data-testid="debug">
      <div className="label">What the tutor reads · {phase ?? "idle"}{tier1Ms != null ? ` · tier 1 ${Math.round(tier1Ms)} ms` : ""}</div>
      {!reading?.lines?.length && <p className="muted">Nothing read yet.</p>}
      {reading?.lines?.map((l) => (
        <div key={l.id} className="line">
          <code>{l.latex ?? l.text ?? JSON.stringify(l.structure)}</code>
          <span className={`conf ${l.confidence < 0.6 ? "low" : ""}`}>{Math.round(l.confidence * 100)}%</span>
        </div>
      ))}
      {reading?.graph && <pre>{reading.graph.nodes?.map((n) => n.label).join(" · ")}</pre>}
    </div>
  );
}
