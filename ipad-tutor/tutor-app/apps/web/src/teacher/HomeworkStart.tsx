// The start screen for a homework link (/?hw=<id>): the homework's title and problem, and the student's
// first name (remembered on this device, easy to change). The session records both.
import { useEffect, useState, type ReactNode } from "react";
import type { HomeworkPublic, Session } from "../protocol";
import { getHomework, loadName, MODE_LABEL, saveName, startHomework } from "./api";
import "./teacher.css";

export function HomeworkStart({ hwId, onStart }: { hwId: string; onStart: (s: Session) => void }) {
  const [hw, setHw] = useState<HomeworkPublic | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [remembered] = useState(loadName);
  const [name, setName] = useState(remembered);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let live = true;
    getHomework(hwId).then(
      (h) => { if (live) setHw(h); },
      () => { if (live) setError("This homework link doesn't work any more. Ask your teacher for a new one."); },
    );
    return () => { live = false; };
  }, [hwId]);

  const start = async () => {
    const n = name.trim().replace(/\s+/g, " ").slice(0, 40);
    if (!hw || !n) return;
    setBusy(true);
    saveName(n);
    try {
      onStart(await startHomework(hw, n));
    } catch {
      setError("Couldn't start. Check that the Mac is on, then try again.");
      setBusy(false);
    }
  };

  return (
    <div className="start">
      <form className="card homework-start" data-testid="homework-start" onSubmit={(e) => { e.preventDefault(); void start(); }}>
        <span className="kicker">Homework{hw ? ` · ${MODE_LABEL[hw.mode] ?? hw.mode}` : ""}</span>
        {!hw && !error && <p className="muted">Loading the homework…</p>}
        {hw && (
          <>
            <h1 data-testid="homework-title">{hw.title}</h1>
            {hw.problem && <p className="homework-problem" data-testid="homework-problem">{hw.problem}</p>}
            {hw.due && <p className="muted">Due {hw.due}</p>}
            <label>Your first name
              <input value={name} onChange={(e) => setName(e.target.value)} maxLength={40} autoComplete="given-name"
                autoCapitalize="words" placeholder="e.g. Marta" data-testid="student-name" />
            </label>
            {remembered && <p className="muted name-note">Remembered on this device. Not {remembered}? Type your own name.</p>}
            <button type="submit" disabled={!name.trim() || busy}>{busy ? "Starting…" : "Start the homework"}</button>
            <p className="muted">Your teacher sees what you practised, the hints you asked for and a short summary.</p>
          </>
        )}
        {error && <p className="err" role="alert">{error}</p>}
      </form>
    </div>
  );
}

/** A homework link: the start screen above until the student starts, then the normal page (`children`). */
export function HomeworkGate({ hwId, children }: { hwId: string; children: (s: Session) => ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  return session ? <>{children(session)}</> : <HomeworkStart hwId={hwId} onStart={setSession} />;
}
