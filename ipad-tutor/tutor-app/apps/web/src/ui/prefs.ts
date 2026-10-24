// Per-device display preferences (localStorage; the page works the same when storage is blocked).
import { useSyncExternalStore } from "react";

const TRANSCRIPT_KEY = "inktutor.showTranscript.v1";
const listeners = new Set<() => void>();

function read(): boolean {
  try { return window.localStorage.getItem(TRANSCRIPT_KEY) !== "0"; } catch { return true; }
}

let showTranscript = read();

export function setShowTranscript(on: boolean): void {
  showTranscript = on;
  try { window.localStorage.setItem(TRANSCRIPT_KEY, on ? "1" : "0"); } catch { /* not remembered */ }
  listeners.forEach((l) => l());
}

/** Whether the voice bubble shows the words (what you said, what it hears, what the tutor said). */
export function useShowTranscript(): boolean {
  return useSyncExternalStore(
    (l) => { listeners.add(l); return () => listeners.delete(l); },
    () => showTranscript,
  );
}

// ---- tutor voice muted: the tutor's words still show in the bubble, its audio isn't played
const MUTED_KEY = "inktutor.muted.v1";
const muteListeners = new Set<() => void>();
let muted = (() => { try { return window.localStorage.getItem(MUTED_KEY) === "1"; } catch { return false; } })();

export function isMuted(): boolean { return muted; }

export function setMuted(on: boolean): void {
  muted = on;
  try { window.localStorage.setItem(MUTED_KEY, on ? "1" : "0"); } catch { /* not remembered */ }
  muteListeners.forEach((l) => l());
}

export function useMuted(): boolean {
  return useSyncExternalStore(
    (l) => { muteListeners.add(l); return () => muteListeners.delete(l); },
    () => muted,
  );
}
