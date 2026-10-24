// Question chips (confidence gate) and the popover with the hint ladder. Long text lives here, never on the page.
// Every "?" also takes a typed (or dictated) answer: "Something else…" on a chip, "Tell me what you meant" in the
// popover of a "?" mark (tutor/Correction.tsx).
import { useState } from "react";
import type { FeedbackEvent } from "../protocol";
import { CorrectionField, type CorrectionSource, type Dictation } from "./Correction";
import { anchorBox, type PageIndex } from "./geometry";

const lineOf = (f: FeedbackEvent) => f.anchor.line_id ?? f.anchor.node_id ?? null;

export function QuestionChips({ feedback, index, onAnswer, onCorrect, dictation, scale = 1 }: {
  feedback: FeedbackEvent[];
  index: PageIndex;
  scale?: number;
  onAnswer: (f: FeedbackEvent, choice: string) => void;
  /** "Something else…": the student types what the line says (a `line.correction`). */
  onCorrect?: (f: FeedbackEvent, text: string, source: CorrectionSource) => void;
  dictation?: Dictation;
}) {
  const [open, setOpen] = useState<string | null>(null); // the chip whose text field is open
  return (
    <>
      {feedback.filter((f) => f.type === "question").map((f) => {
        const b = anchorBox(f.anchor, index);
        if (!b) return null;
        const line = lineOf(f);
        const correcting = open === f.id && onCorrect && line;
        return (
          <div key={f.id} className={`chip${correcting ? " correcting" : ""}`} data-feedback={f.id}
            style={{ left: b.x * scale, top: (b.y + b.h) * scale + 8 }}>
            <span>{f.text}</span>
            {(f.choices?.length ? f.choices : ["Yes", "No"]).map((c) => (
              <button key={c} onClick={() => onAnswer(f, c)}>{c}</button>
            ))}
            {onCorrect && line && !correcting && (
              <button className="else" onClick={() => setOpen(f.id)}>Something else…</button>
            )}
            {correcting && (
              <>
                <span className="correction-label">What did you write?</span>
                <CorrectionField lineId={line} label="What did you write?" dictation={dictation}
                  onSend={(text, source) => { setOpen(null); onCorrect(f, text, source); }} />
                <button className="secondary" onClick={() => setOpen(null)}>Cancel</button>
              </>
            )}
          </div>
        );
      })}
    </>
  );
}

export const LEVELS = ["", "Nudge", "Hint", "Corrected step", "Full solution"];

function ladderKey(f: FeedbackEvent): string {
  return f.anchor.line_id ?? f.anchor.node_id ?? (f.anchor.stroke_ids ?? []).join(",");
}

export function Popover({ target, at, feedback, pageWidth, onHint, onDismiss, onGhost, onClose, accepted, onCorrect, dictation }: {
  target: FeedbackEvent;
  at: { x: number; y: number };
  feedback: FeedbackEvent[];
  pageWidth: number;
  accepted: boolean;
  onHint: (level: number, explicit: boolean) => void;
  onDismiss: () => void;
  onGhost: (accept: boolean) => void;
  onClose: () => void;
  /** "?" marks: "Tell me what you meant" (a `line.correction` for the mark's line). */
  onCorrect?: (text: string, source: CorrectionSource) => void;
  dictation?: Dictation;
}) {
  const [confirmSolution, setConfirmSolution] = useState(false);
  const key = ladderKey(target);
  const hints = feedback
    .filter((f) => (f.type === "hint" || f.type === "explain") && ladderKey(f) === key)
    .sort((a, b) => (a.hint_level ?? 0) - (b.hint_level ?? 0));
  const level = Math.max(0, ...hints.map((h) => h.hint_level ?? 0));
  const left = Math.min(Math.max(8, at.x - 150), pageWidth - 316);
  return (
    <div className="popover" style={{ left, top: at.y + 18 }} role="dialog" data-feedback={target.id}>
      <button className="close" aria-label="Close" onClick={onClose}>×</button>
      {target.type === "ghost" ? (
        <>
          <div className="label">The tutor's suggestion</div>
          <p className="ghost-text">{target.text}</p>
          <div className="row">
            {!accepted && <button onClick={() => onGhost(true)}>Keep</button>}
            <button className="secondary" onClick={() => onGhost(false)}>Dismiss</button>
          </div>
        </>
      ) : (
        <>
          {target.text && <p className="mark-text">{target.text}</p>}
          {hints.map((h) => (
            <div key={h.id} className="hint">
              <div className="label">{LEVELS[h.hint_level ?? 1]}</div>
              <p>{h.text}</p>
            </div>
          ))}
          <div className="row">
            {level < 3 && (
              <button onClick={() => onHint(level + 1, false)}>{level === 0 ? "Hint" : "More"}</button>
            )}
            {level === 3 && !confirmSolution && (
              <button className="secondary" onClick={() => setConfirmSolution(true)}>Show the full solution…</button>
            )}
            {confirmSolution && (
              <button className="danger" onClick={() => { setConfirmSolution(false); onHint(4, true); }}>
                Yes, show the full solution
              </button>
            )}
            <button className="secondary" onClick={onDismiss}>Dismiss mark</button>
          </div>
          {onCorrect && target.type === "mark" && target.glyph === "question" && lineOf(target) && (
            <div className="correction-block">
              <div className="label">Tell me what you meant</div>
              <CorrectionField lineId={lineOf(target)!} label="Tell me what you meant" dictation={dictation}
                autoFocus={false} onSend={onCorrect} />
            </div>
          )}
        </>
      )}
    </div>
  );
}
