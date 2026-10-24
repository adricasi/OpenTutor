// /teacher: set homework, share its link / QR with the students, see who did it and a short
// summary of each. Opens on the Mac or the LAN only (the server refuses it through the tunnel).
import { useCallback, useEffect, useState } from "react";
import type { HomeworkCreate, HomeworkProgress, SessionBrief, StudentRow } from "../protocol";
import {
  createHomework, csvUrl, deleteHomework, homeworkProgress, listHomework, looseSessions, minutes, MODE_LABEL,
  STATUS_LABEL, type HomeworkItem,
} from "./api";
import { TutorText } from "../tutor/TutorText";
import { SessionView } from "./SessionView";
import "./teacher.css";

const NONE = "none"; // "sessions without homework"

export function StudentTable({ rows, onOpen }: { rows: StudentRow[]; onOpen: (sessionId: string) => void }) {
  if (!rows.length) return <p className="muted" data-testid="no-students">Nobody has opened this homework yet.</p>;
  return (
    <table className="teacher-table" data-testid="student-table">
      <thead>
        <tr><th>Student</th><th>Did it</th><th>Time</th><th>Hints</th><th>Mistakes fixed</th><th>Summary</th></tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.session_id} onClick={() => onOpen(r.session_id)} tabIndex={0}
            onKeyDown={(e) => { if (e.key === "Enter") onOpen(r.session_id); }} data-status={r.status}>
            <td className="name">{r.student}{(r.sessions?.length ?? 1) > 1 && <span className="muted"> · {r.sessions!.length} tries</span>}</td>
            <td><span className={`status ${r.status}`}>{STATUS_LABEL[r.status] ?? r.status}</span></td>
            <td>{minutes(r.time_on_task_s)}</td>
            <td>{r.hints ?? 0}{r.hints_explicit ? <span className="muted"> (solution ×{r.hints_explicit})</span> : null}</td>
            <td>{(r.mistakes ?? 0) ? `${r.mistakes_fixed ?? 0} of ${r.mistakes}` : "–"}</td>
            <td className="summary"><TutorText text={r.summary?.text} /></td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function HomeworkLink({ progress }: { progress: HomeworkProgress }) {
  const [copied, setCopied] = useState(false);
  const lan = !progress.link.includes("trycloudflare.com");
  return (
    <section className="homework-link" data-testid="homework-link">
      <img src={progress.qr} alt="QR code of the homework link" width={148} height={148} />
      <div>
        <h3>Link for students</h3>
        <p className="link-url"><a href={progress.link} target="_blank" rel="noreferrer">{progress.link}</a></p>
        <button onClick={() => {
          void navigator.clipboard?.writeText(progress.link).then(() => setCopied(true), () => setCopied(false));
        }}>{copied ? "Copied" : "Copy link"}</button>
        <p className="muted">
          {lan
            ? "This is the Wi-Fi (LAN) link. For iPads on another network, run make pair (or make ipad) and reload: the link becomes the HTTPS tunnel one."
            : "HTTPS tunnel link (make pair): works from any network while the Mac runs it."}
          {" "}Students type their first name; it is remembered on their device.
        </p>
      </div>
    </section>
  );
}

function NewHomework({ onCreated }: { onCreated: (id: string) => void }) {
  const [f, setF] = useState<HomeworkCreate>({ title: "", mode: "math", problem: "", due: null, answer: null });
  const [error, setError] = useState<string | null>(null);
  const set = (patch: Partial<HomeworkCreate>) => setF({ ...f, ...patch });
  return (
    <form className="card new-homework" data-testid="new-homework" onSubmit={(e) => {
      e.preventDefault();
      createHomework({ ...f, due: f.due || null, answer: f.answer?.trim() || null }).then(
        (hw) => { setF({ title: "", mode: f.mode, problem: "", due: null, answer: null }); setError(null); onCreated(hw.id); },
        (err: Error) => setError(err.message),
      );
    }}>
      <h3>New homework</h3>
      <label>Title<input value={f.title} onChange={(e) => set({ title: e.target.value })} maxLength={120} required placeholder="e.g. Definite integrals" /></label>
      <label>Subject
        <select value={f.mode} onChange={(e) => set({ mode: e.target.value as HomeworkCreate["mode"] })}>
          {Object.entries(MODE_LABEL).filter(([v]) => v !== "diagram").map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>
      </label>
      <label>The problem (what the students and the tutor see)
        <textarea value={f.problem} onChange={(e) => set({ problem: e.target.value })} rows={3} maxLength={2000} placeholder="e.g. Compute ∫₀¹ x³ dx. Show your steps." />
      </label>
      <div className="row">
        <label>Due (optional)<input type="date" value={f.due ?? ""} onChange={(e) => set({ due: e.target.value || null })} /></label>
        <label>Final answer (optional)<input value={f.answer ?? ""} onChange={(e) => set({ answer: e.target.value })} maxLength={200} placeholder="e.g. 1/4" /></label>
      </div>
      <p className="muted">With a final answer, a student counts as done when a line checked ✓ reaches it; otherwise when their last checked line is ✓.</p>
      <button type="submit" disabled={!f.title.trim()}>Create homework</button>
      {error && <p className="err">{error}</p>}
    </form>
  );
}

function LooseSessions({ items, onOpen }: { items: SessionBrief[]; onOpen: (id: string) => void }) {
  if (!items.length) return <p className="muted">No sessions without homework yet.</p>;
  return (
    <table className="teacher-table" data-testid="loose-table">
      <thead><tr><th>When</th><th>Name</th><th>Subject</th><th>Time</th><th>Hints</th><th>Summary</th></tr></thead>
      <tbody>
        {items.map((s) => (
          <tr key={s.session_id} onClick={() => onOpen(s.session_id)} tabIndex={0} onKeyDown={(e) => { if (e.key === "Enter") onOpen(s.session_id); }}>
            <td>{s.created ? new Date(s.created * 1000).toLocaleString() : ""}</td>
            <td className="name">{s.student ?? <span className="muted">–</span>}</td>
            <td>{MODE_LABEL[s.mode] ?? s.mode}{s.problem ? <span className="muted"> · {s.problem.slice(0, 40)}</span> : null}</td>
            <td>{minutes(s.log.time_on_task_s)}</td>
            <td>{(s.log.hints ?? []).reduce((a, b) => a + b, 0)}</td>
            <td className="summary"><TutorText text={s.summary?.text} /></td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function TeacherPage({ pollMs = 15000 }: { pollMs?: number }) {
  const [items, setItems] = useState<HomeworkItem[] | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [progress, setProgress] = useState<HomeworkProgress | null>(null);
  const [loose, setLoose] = useState<SessionBrief[] | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(() => {
    listHomework().then((list) => {
      setItems(list);
      setError(null);
      setSelected((cur) => cur ?? list[0]?.homework.id ?? NONE);
    }, (e: Error) => setError(e.message));
  }, []);

  useEffect(() => { reload(); }, [reload]);

  useEffect(() => {
    if (!selected || open) return;
    let live = true;
    const load = () => {
      if (selected === NONE) looseSessions().then((l) => { if (live) setLoose(l); }, (e: Error) => { if (live) setError(e.message); });
      else homeworkProgress(selected).then((p) => { if (live) setProgress(p); }, (e: Error) => { if (live) setError(e.message); });
    };
    load();
    const t = setInterval(load, pollMs);
    return () => { live = false; clearInterval(t); };
  }, [selected, open, pollMs]);

  const current = selected && selected !== NONE && progress?.homework.id === selected ? progress : null;

  return (
    <div className="teacher" data-testid="teacher-page">
      <header className="teacher-bar">
        <h1>Ink Tutor · Teacher</h1>
        <span className="muted">On this Mac only. Children's work never leaves it.</span>
      </header>
      {error && <p className="err teacher-error" role="alert">{error}</p>}
      {open ? <SessionView sessionId={open} onClose={() => { setOpen(null); reload(); }} /> : (
        <div className="teacher-main">
          <nav className="teacher-list">
            <NewHomework onCreated={(id) => { setSelected(id); reload(); }} />
            <h3>Homework</h3>
            {items == null && !error && <p className="muted">Loading…</p>}
            {items?.length === 0 && <p className="muted">None yet: create one above.</p>}
            <ul>
              {items?.map((it) => (
                <li key={it.homework.id}>
                  <button className={selected === it.homework.id ? "on" : ""} onClick={() => setSelected(it.homework.id)}>
                    {it.homework.title}<span className="count">{it.done}/{it.students}</span>
                  </button>
                </li>
              ))}
              <li><button className={selected === NONE ? "on" : ""} onClick={() => setSelected(NONE)}>Sessions without homework</button></li>
            </ul>
          </nav>
          <main className="teacher-content">
            {current && (
              <>
                <div className="teacher-hw-head">
                  <div>
                    <h2>{current.homework.title}</h2>
                    <p className="muted">
                      {MODE_LABEL[current.homework.mode ?? "math"]}
                      {current.homework.due ? ` · due ${current.homework.due}` : ""}
                      {current.homework.answer ? ` · final answer ${current.homework.answer}` : ""}
                    </p>
                    {current.homework.problem && <p className="homework-problem">{current.homework.problem}</p>}
                  </div>
                  <div className="row">
                    <a className="button" href={csvUrl(current.homework.id)} download>Export CSV</a>
                    <button className="secondary" onClick={() => {
                      if (!window.confirm(`Delete “${current.homework.title}”? The students' sessions stay (under sessions without homework).`)) return;
                      void deleteHomework(current.homework.id).then(() => { setSelected(null); setProgress(null); reload(); });
                    }}>Delete</button>
                  </div>
                </div>
                <HomeworkLink progress={current} />
                <StudentTable rows={current.rows ?? []} onOpen={setOpen} />
              </>
            )}
            {selected === NONE && (
              <>
                <div className="teacher-hw-head">
                  <h2>Sessions without homework</h2>
                  <a className="button" href={csvUrl(NONE)} download>Export CSV</a>
                </div>
                {loose ? <LooseSessions items={loose} onOpen={setOpen} /> : <p className="muted">Loading…</p>}
              </>
            )}
          </main>
        </div>
      )}
    </div>
  );
}
