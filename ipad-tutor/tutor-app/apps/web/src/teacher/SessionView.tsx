// A student's session, read-only: their ink replayed stroke by stroke with the tutor's marks (the same
// TutorLayer as the live page), the learning log and the summary. Nothing here can write to the session.
import { useEffect, useMemo, useRef, useState } from "react";
import type { FeedbackEvent, Stroke } from "../protocol";
import { drawStroke } from "../ink/draw";
import { TutorLayer } from "../tutor/TutorLayer";
import { visibleFeedback, type PageIndex } from "../tutor/geometry";
import { minutes, MODE_LABEL, refreshSummary, sessionDetail, type SessionDetail } from "./api";
import { TutorText } from "../tutor/TutorText";

const ERASED = "#b8c0c8";

/** Marks whose anchor strokes have all been drawn by stroke `n` of the replay (bbox-only anchors: at the end). */
export function marksAt(fb: FeedbackEvent[], order: Stroke[], n: number, lineStrokes: Map<string, string[]>): FeedbackEvent[] {
  const drawn = new Set(order.slice(0, n).map((s) => s.id));
  const all = new Set(order.map((s) => s.id));
  const done = n >= order.length;
  return fb.filter((f) => {
    const ids = f.anchor.stroke_ids?.length ? f.anchor.stroke_ids : lineStrokes.get(f.anchor.line_id ?? "") ?? null;
    if (!ids) return done;
    return ids.every((i) => drawn.has(i) || !all.has(i)) && (done || ids.some((i) => drawn.has(i)));
  });
}

export function SessionView({ sessionId, onClose }: { sessionId: string; onClose: () => void }) {
  const [data, setData] = useState<SessionDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pageNo, setPageNo] = useState(0);
  const [n, setN] = useState<number | null>(null); // replay position (strokes drawn); null = the whole page
  const [playing, setPlaying] = useState(false);
  const [showErased, setShowErased] = useState(false);
  const [summaryNote, setSummaryNote] = useState<string | null>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const nRef = useRef<number | null>(null);
  useEffect(() => { nRef.current = n; }, [n]);

  useEffect(() => {
    let live = true;
    sessionDetail(sessionId).then((d) => { if (live) setData(d); }, (e: Error) => { if (live) setError(e.message); });
    return () => { live = false; };
  }, [sessionId]);

  const page = data?.pages[pageNo] ?? null;
  const erased = useMemo(() => new Set(page?.erased ?? []), [page]);
  // the page as it ended, in drawing order (erased strokes only when asked for, faded)
  const order = useMemo(
    () => (page?.strokes ?? []).filter((s) => showErased || !erased.has(s.id)).sort((a, b) => (a.pts[0]?.[0] ?? 0) - (b.pts[0]?.[0] ?? 0)),
    [page, erased, showErased],
  );
  const shown = n == null ? order.length : Math.min(n, order.length);

  useEffect(() => {
    const c = canvasRef.current;
    const ctx = c?.getContext?.("2d");
    if (!c || !ctx || !page) return;
    const dpr = window.devicePixelRatio || 1;
    c.width = page.page.width * dpr;
    c.height = page.page.height * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    ctx.clearRect(0, 0, page.page.width, page.page.height);
    for (const s of order.slice(0, shown)) drawStroke(ctx, erased.has(s.id) ? { ...s, color: ERASED } : s);
  }, [page, order, shown, erased]);

  useEffect(() => {
    if (!playing) return;
    const step = Math.max(1, Math.ceil(order.length / 200)); // the whole page in about 10 s
    const t = setInterval(() => {
      const next = Math.min(order.length, (nRef.current ?? 0) + step);
      nRef.current = next;
      setN(next);
      if (next >= order.length) setPlaying(false);
    }, 50);
    return () => clearInterval(t);
  }, [playing, order.length]);

  if (error) return <div className="teacher-session card"><p className="err">{error}</p><button onClick={onClose}>Back</button></div>;
  if (!data || !page) return <div className="teacher-session card"><p className="muted">Loading the session…</p></div>;

  const lg = data.log;
  const fb = visibleFeedback(page.feedback);
  const lineStrokes = new Map((page.reading?.lines ?? []).map((l) => [l.id, l.stroke_ids]));
  const marks = marksAt(fb, order, shown, lineStrokes);
  const strokes = new Map(order.map((s) => [s.id, s]));
  const index: PageIndex = { strokes, reading: page.reading };
  const hints = page.feedback.filter((f) => f.type === "hint" && f.text);
  const cloud = data.session.provider === "anthropic" || data.session.provider === "openai";
  const h = lg.hints ?? [0, 0, 0, 0];

  const refresh = async (useCloud: boolean) => {
    setSummaryNote("Writing…");
    try {
      const r = await refreshSummary(sessionId, useCloud);
      setData({ ...data, summary: r.summary });
      setSummaryNote(r.queued ? "A student is working right now: the tutor rewrites this when they pause." : null);
    } catch (e) {
      setSummaryNote((e as Error).message);
    }
  };

  return (
    <div className="teacher-session" data-testid="session-view">
      <header className="teacher-session-head">
        <button onClick={onClose}>← Back</button>
        <h2>{data.session.student ?? "No name"} · {data.homework?.title ?? MODE_LABEL[data.session.mode] ?? data.session.mode}</h2>
        <span className="muted">read-only</span>
      </header>
      <div className="teacher-session-body">
        <div className="teacher-replay">
          <div className="replay-controls">
            <button onClick={() => { if (shown >= order.length) { nRef.current = 0; setN(0); } setPlaying(!playing); }} disabled={!order.length}>
              {playing ? "Pause" : "Replay"}
            </button>
            <input type="range" min={0} max={order.length} value={shown} aria-label="Replay position"
              onChange={(e) => { setPlaying(false); setN(Number(e.target.value)); }} />
            <label className="check"><input type="checkbox" checked={showErased} onChange={(e) => setShowErased(e.target.checked)} /> Erased ink</label>
            {data.pages.length > 1 && (
              <select value={pageNo} onChange={(e) => { setPageNo(Number(e.target.value)); setN(null); setPlaying(false); }}>
                {data.pages.map((p, i) => <option key={p.page.id} value={i}>Page {i + 1}</option>)}
              </select>
            )}
          </div>
          <div className="teacher-paper" style={{ width: page.page.width, height: page.page.height }}>
            <canvas ref={canvasRef} style={{ width: page.page.width, height: page.page.height }} />
            <TutorLayer page={page.page} feedback={marks} index={index} checking={[]} accepted={new Set()} onOpen={() => {}} />
          </div>
        </div>
        <aside className="teacher-facts">
          <section>
            <h3>Summary</h3>
            <p data-testid="session-summary"><TutorText text={data.summary.text} /></p>
            <p className="muted">{data.summary.source === "template" ? "Written from the log (template)" : `Written by ${data.summary.source}`}</p>
            <div className="row">
              <button onClick={() => void refresh(false)}>Refresh</button>
              {cloud && <button className="secondary" onClick={() => void refresh(true)} title="Sends the learning log (no ink) to the session's cloud provider">Refresh with {data.session.provider === "anthropic" ? "Claude" : "OpenAI"}</button>}
            </div>
            {summaryNote && <p className="muted">{summaryNote}</p>}
          </section>
          <section>
            <h3>Learning log</h3>
            <dl className="facts">
              <dt>Time on task</dt><dd>{minutes(lg.time_on_task_s)}</dd>
              <dt>Lines written · checked</dt><dd>{lg.lines_written ?? 0} · {lg.lines_checked ?? 0}</dd>
              <dt>Mistakes · fixed</dt><dd>{lg.mistakes ?? 0} · {lg.mistakes_fixed ?? 0}</dd>
              <dt>Hints (nudge · hint · step · solution)</dt><dd>{h.join(" · ")}</dd>
              <dt>Questions answered</dt><dd>{lg.questions_answered ?? 0} of {lg.questions_asked ?? 0}</dd>
              <dt>Corrections typed</dt><dd>{lg.corrections ?? 0}</dd>
              <dt>Finished</dt><dd>{lg.finished == null ? "no automatic check" : lg.finished ? "yes" : "not yet"}</dd>
            </dl>
            {(lg.verdicts ?? []).length > 0 && (
              <ul className="verdicts">{lg.verdicts!.map((v) => <li key={v.kind}><code>{v.kind}</code> ✓ {v.ok ?? 0} · ✗ {v.wrong ?? 0}</li>)}</ul>
            )}
          </section>
          {hints.length > 0 && (
            <section><h3>Hints they got</h3>
              <ol className="hints">{hints.map((f) => <li key={f.id}><span className="muted">level {f.hint_level}</span> <TutorText text={f.text} /></li>)}</ol>
            </section>
          )}
        </aside>
      </div>
    </div>
  );
}
