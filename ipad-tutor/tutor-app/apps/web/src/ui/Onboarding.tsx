// First-run intro on the start screen (shown once; re-open from Settings → "Show the intro again").
import { useEffect, useState } from "react";
import "./notices.css";

export const ONBOARDED_KEY = "inktutor.onboarded.v1";

// Storage can throw (private mode, blocked site data): the intro then simply shows and isn't remembered.
export function shouldShowOnboarding(): boolean {
  try { return window.localStorage.getItem(ONBOARDED_KEY) !== "1"; } catch { return true; }
}

export function markOnboarded(): void {
  try { window.localStorage.setItem(ONBOARDED_KEY, "1"); } catch { /* not remembered */ }
}

export const STEPS: { title: string; body: string }[] = [
  {
    title: "Write with the Pencil",
    body: "Work on the page the way you would on paper. Your ink is never changed or tidied up. Your palm and fingers don't draw (turn on Fingers in the toolbar if you want them to).",
  },
  {
    title: "The tutor marks your lines",
    body: "A few seconds after you lift the pen, small marks appear beside your lines: ✓ right, ✗ wrong, ? not sure. Tap a mark for a hint, then More for a bigger one. The full solution only comes when you ask for it.",
  },
  {
    title: "Talk to it",
    body: "Tap Talk, the round mic button at the bottom right of the page, and ask out loud: “why is this wrong?” or “just a hint”. It says “Listening…” with a level bar while it hears you; tap Stop or just talk to interrupt it. On the iPad the microphone only works when the page is opened over HTTPS.",
  },
  {
    title: "Settings: who tutors you",
    body: "Settings picks the tutor: the local model on this Mac (default) or Claude / OpenAI. Reading your handwriting always stays on the Mac. The badge shows who answered; the memory light turns amber or red when the Mac is short of memory.",
  },
];

export function Onboarding({ onClose }: { onClose: () => void }) {
  const [i, setI] = useState(0);
  const last = i === STEPS.length - 1;
  const step = STEPS[i]!;
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="intro-backdrop" data-testid="onboarding">
      <div className="intro card" role="dialog" aria-modal="true" aria-labelledby="intro-title">
        <div className="intro-step muted">{i + 1} of {STEPS.length}</div>
        <h2 id="intro-title">{step.title}</h2>
        <p>{step.body}</p>
        <div className="intro-dots" aria-hidden="true">
          {STEPS.map((s, k) => <span key={s.title} className={k === i ? "on" : ""} />)}
        </div>
        <div className="intro-row">
          <button className="secondary" onClick={onClose}>Skip</button>
          <span className="intro-grow" />
          {i > 0 && <button onClick={() => setI(i - 1)}>Back</button>}
          <button className="on" onClick={() => (last ? onClose() : setI(i + 1))}>{last ? "Start" : "Next"}</button>
        </div>
      </div>
    </div>
  );
}
