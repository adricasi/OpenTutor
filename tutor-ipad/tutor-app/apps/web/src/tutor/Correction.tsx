// "Tell the tutor what you meant": a small text field on every "?" (question chips, "?" marks) where the student
// types (or, with voice on, says) what a line really reads. Only the tutor's reading changes, never the ink.
// The field is a normal <input>: the iPad keyboard and Scribble (Apple Pencil handwriting into text fields) work
// in it, text can be selected (the app turns user-select off globally), and pen events never reach the canvas.
import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import type { ClientMessage, FeedbackEvent } from "../protocol";
import "./correction.css";

export type CorrectionSource = "typed" | "spoken";

/** Voice on: the mic asks the server to keep the next utterance for this field (`voice.dictate`); the next final
 *  transcript (`state.transcripts` gets a new array) fills it, to be edited and sent. */
export interface Dictation {
  transcripts: string[];
  onDictate: (lineId: string, cancel: boolean) => void;
}

/** What App wires into QuestionChips / Popover: the `line.correction` + `voice.dictate` messages. */
export function useCorrection(
  send: (m: ClientMessage) => void, pageId: string, voiceOn: boolean, transcripts: string[],
): { onCorrect: (f: FeedbackEvent, text: string, source: CorrectionSource) => void; dictation?: Dictation } {
  const onCorrect = useCallback((f: FeedbackEvent, text: string, source: CorrectionSource) => {
    const lineId = f.anchor.line_id ?? f.anchor.node_id;
    if (lineId) send({ type: "line.correction", page_id: pageId, line_id: lineId, text, source });
  }, [send, pageId]);
  const dictation = useMemo<Dictation | undefined>(() => voiceOn
    ? { transcripts, onDictate: (lineId, cancel) => send({ type: "voice.dictate", page_id: pageId, line_id: lineId, cancel }) }
    : undefined, [send, pageId, voiceOn, transcripts]);
  return { onCorrect, dictation };
}

// Pointer/touch input stays with the field: nothing above it (the page, ink handlers) may start a stroke from it.
// Native listeners, so ancestors' native listeners don't see the events either (React's own stopPropagation
// runs at the root, after them). No preventDefault: the keyboard, caret, selection and Scribble need the defaults.
const PEN_EVENTS = ["pointerdown", "pointermove", "pointerup", "touchstart", "touchmove", "touchend"] as const;
const keep = (e: Event) => e.stopPropagation();

export function CorrectionField({ lineId, label, onSend, dictation, autoFocus = true }: {
  lineId: string;
  label: string;
  onSend: (text: string, source: CorrectionSource) => void;
  dictation?: Dictation;
  autoFocus?: boolean;
}) {
  const [text, setText] = useState("");
  const [source, setSource] = useState<CorrectionSource>("typed");
  // the transcripts array when the mic was pressed: a different array means a new utterance arrived
  const [listening, setListening] = useState<string[] | null>(null);
  if (listening && dictation && dictation.transcripts !== listening) {
    setListening(null); // adjusting state while rendering: the dictated words fill the field once
    setText(dictation.transcripts[dictation.transcripts.length - 1] ?? "");
    setSource("spoken");
  }
  if (listening && !dictation) setListening(null); // voice was switched off

  const formRef = useRef<HTMLFormElement>(null);
  useEffect(() => {
    const el = formRef.current;
    if (!el) return;
    for (const t of PEN_EVENTS) el.addEventListener(t, keep);
    return () => { for (const t of PEN_EVENTS) el.removeEventListener(t, keep); };
  }, []);
  // closing the field while listening tells the server the next utterance is a question again
  const pending = useRef<(() => void) | null>(null);
  useEffect(() => {
    pending.current = listening && dictation ? () => dictation.onDictate(lineId, true) : null;
  }, [listening, dictation, lineId]);
  useEffect(() => () => pending.current?.(), []);

  const submit = (e: FormEvent) => {
    e.preventDefault();
    e.stopPropagation();
    const t = text.trim();
    if (!t) return;
    if (listening) { dictation?.onDictate(lineId, true); setListening(null); }
    onSend(t, source);
    setText("");
    setSource("typed");
  };
  const mic = () => {
    if (!dictation) return;
    if (listening) { dictation.onDictate(lineId, true); setListening(null); return; }
    dictation.onDictate(lineId, false);
    setText("");
    setListening(dictation.transcripts);
  };

  return (
    <form ref={formRef} className="correction" onSubmit={submit} data-testid="correction">
      <input type="text" value={text} aria-label={label} placeholder={listening ? "listening…" : label}
        autoFocus={autoFocus} enterKeyHint="send" inputMode="text" autoComplete="off" autoCorrect="off"
        autoCapitalize="off" spellCheck={false} maxLength={500}
        onChange={(e) => { setText(e.target.value); if (!e.target.value) setSource("typed"); }} />
      {dictation && (
        <button type="button" className={`mic${listening ? " on" : ""}`} onClick={mic}
          aria-label={listening ? "Stop listening" : "Say it"} title={listening ? "Stop listening" : "Say it instead"}>
          <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
            <rect x="5.5" y="1.5" width="5" height="8.5" rx="2.5" fill={listening ? "currentColor" : "none"} stroke="currentColor" strokeWidth="1.5" />
            <path d="M3 8a5 5 0 0 0 10 0M8 13v2" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
          </svg>
        </button>
      )}
      <button type="submit" disabled={!text.trim()}>Send</button>
    </form>
  );
}
