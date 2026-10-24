// The Talk button nobody can miss: a round mic button floating at the bottom right of the page (never in the scrolling
// toolbar), a bubble with what it is doing (listening + level meter, tutor speaking + Stop, loading, errors) and the
// last things said. Plus the compact toolbar entry. The bubble lets the pen write through it; only buttons take taps.
import { useEffect, useState, type CSSProperties } from "react";
import type { VoiceControl, VoicePhase, VoiceView } from "../voice/control";
import { levelFraction } from "../voice/voice";
import "./voice.css";

const LABEL: Record<VoicePhase, string> = {
  off: "Talk", starting: "Allow the mic…", loading: "Loading voice…", listening: "Listening…", speaking: "Tutor speaking",
};
const SHORT: Record<VoicePhase, string> = {
  off: "Talk", starting: "Mic…", loading: "Loading…", listening: "Listening", speaking: "Speaking",
};

export function MicIcon() {
  return (
    <svg className="mic-icon" viewBox="0 0 24 24" width="22" height="22" aria-hidden="true">
      <rect x="9" y="3" width="6" height="11" rx="3" fill="currentColor" />
      <path d="M6 11a6 6 0 0 0 12 0M12 17v4M8.5 21h7" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
    </svg>
  );
}

/** 0..1 mic level while listening (~10 updates per second); 0 otherwise. */
export function useLevel(control: VoiceControl, active: boolean): number {
  const [level, setLevel] = useState(0);
  useEffect(() => {
    if (!active) return;
    const off = control.subscribeLevel((rms) => setLevel(levelFraction(rms)));
    return () => { off(); setLevel(0); };
  }, [control, active]);
  return active ? level : 0;
}

/** Five bars, lit up to the level: "I can hear you". */
export function LevelMeter({ level }: { level: number }) {
  const lit = Math.round(level * 5);
  return (
    <span className="level-meter" role="meter" aria-label="Microphone level" aria-valuemin={0} aria-valuemax={100}
      aria-valuenow={Math.round(level * 100)} data-testid="level-meter">
      {[0, 1, 2, 3, 4].map((i) => <span key={i} className={i < lit ? "on" : ""} style={{ height: `${6 + i * 3}px` }} />)}
    </span>
  );
}

export function VoiceDock({ view, control, transcripts, tutorSaid }: {
  view: VoiceView; control: VoiceControl; transcripts: string[]; tutorSaid: string | null;
}) {
  const { phase } = view;
  const on = phase !== "off";
  const level = useLevel(control, phase === "listening" || phase === "speaking");
  const busy = phase === "starting" || phase === "loading";
  const lines = on ? transcripts.slice(-2) : [];
  const showBubble = on || !!view.note;
  return (
    <div className="voice-dock" data-phase={phase} data-testid="voice-dock">
      {showBubble && (
        <div className="voice-bubble" aria-live="polite" data-testid="voice-bubble">
          {on && (
            <div className="voice-status">
              <strong>{LABEL[phase]}</strong>
              {(phase === "listening" || phase === "speaking") && <LevelMeter level={level} />}
            </div>
          )}
          {view.note && (
            <p className="voice-note" data-testid="voice-note">
              {view.note}
              {!on && <button className="voice-x" aria-label="Hide" onClick={control.clearNote}>×</button>}
            </p>
          )}
          {phase === "listening" && !view.heard && lines.length === 0 && <p className="muted">Ask out loud: “why is this wrong?” or “just a hint”.</p>}
          {lines.map((t, i) => <p key={`${i}:${t}`} className="said">“{t}”</p>)}
          {view.heard && <p className="heard">“{view.heard}…”</p>}
          {on && tutorSaid && <p className="tutor-said">{tutorSaid}</p>}
          {view.blocked && on && <p className="voice-blocked">Sound is paused by the browser: tap anywhere to hear the tutor.</p>}
        </div>
      )}
      <div className="voice-actions">
        {phase === "speaking" && (
          <button className="voice-stop" onClick={control.stopSpeaking} data-testid="voice-stop" aria-label="Stop the tutor talking">
            <span className="stop-square" aria-hidden="true" />Stop
          </button>
        )}
        <button className={`voice-fab ${phase}`} onClick={control.toggle} data-testid="talk" aria-pressed={on}
          aria-label={on ? `${LABEL[phase]} Tap to turn voice off.` : "Talk to the tutor"}
          title={on ? "Tap to turn voice off" : "Talk to the tutor (interrupt it any time)"}
          style={{ "--level": level } as CSSProperties}>
          <span className={busy ? "fab-spin" : "fab-icon"} aria-hidden="true">{busy ? null : <MicIcon />}</span>
          <span className="fab-text">
            {LABEL[phase]}
            {!on && view.unavailable && <small>needs HTTPS</small>}
          </span>
        </button>
      </div>
    </div>
  );
}

/** Compact toolbar entry (icon-only on narrow screens; the floating button is the main one). */
export function TalkButton({ view, onClick }: { view: VoiceView; onClick: () => void }) {
  const on = view.phase !== "off";
  return (
    <button className={`talk-mini ${view.phase}${on ? " on" : ""}`} onClick={onClick} data-testid="voice" aria-pressed={on}
      aria-label={on ? `${LABEL[view.phase]} Tap to turn voice off.` : "Talk to the tutor"}
      title={view.unavailable ?? "Talk to the tutor (interrupt it any time)"}>
      <MicIcon /><span className="talk-label">{SHORT[view.phase]}</span>
    </button>
  );
}
