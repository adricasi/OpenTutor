// Words for error states and the memory light: pure functions so the reducer, the UI and the tests share them.
import type { RuntimeStatus } from "../protocol";

export type ToastKind = "info" | "warn" | "error";
export interface Notice { kind: ToastKind; title: string; text?: string; key: string }

/** A StatusMsg with phase="error" (or an ErrorMsg) → what the student sees. */
export function describeError(detail: string): Notice & { voice?: boolean } {
  const d = detail.trim();
  if (/^voice:/i.test(d)) {
    const why = d.replace(/^voice:\s*/i, "");
    const tip = /not enough memory/i.test(why) ? " Free some memory (close other apps) and tap Talk again."
      : /not downloaded/i.test(why) ? " The voice models need `make models` on the Mac." : " Tap Talk to try again.";
    return { kind: "warn", title: "Voice couldn't start", text: why + tip, key: "voice", voice: true };
  }
  if (/unavailable.*answering locally/i.test(d)) {
    return { kind: "warn", title: "Cloud tutor unavailable", text: "Answering with the local model on this Mac instead.", key: "fallback" };
  }
  if (/explicit request/i.test(d)) {
    return { kind: "info", title: "The full solution is only shown when you ask for it", text: "Tap a mark, then “Show the full solution…”.", key: "explicit" };
  }
  if (/photo/i.test(d)) return { kind: "error", title: "Couldn't read the photo", text: d, key: "photo" };
  if (/not enough memory/i.test(d)) return { kind: "warn", title: "Not enough memory", text: d, key: "memory" };
  if (/^tutor:/i.test(d)) {
    return { kind: "error", title: "The tutor had a problem", text: `${d.replace(/^tutor:\s*/i, "")} — your ink is safe; keep writing and it will try again.`, key: "tutor" };
  }
  return { kind: "error", title: "Something went wrong", text: d || undefined, key: `err:${d.slice(0, 40)}` };
}

/** getUserMedia failures → a sentence that says what to do. The mic only works on a secure (HTTPS) page. */
export function micErrorMessage(e: unknown): string {
  const name = (e as { name?: string } | null)?.name ?? "";
  if (name === "NotAllowedError" || name === "SecurityError") {
    return "Microphone permission was denied. In Safari: aA → Website Settings → Microphone → Allow, then tap Talk again. On the iPad the page must be opened over HTTPS.";
  }
  if (name === "NotFoundError" || name === "OverconstrainedError") return "No microphone was found on this device.";
  if (name === "NotReadableError") return "The microphone is in use by another app.";
  return `Microphone: ${(e as Error | null)?.message ?? String(e)}`;
}

export type Light = "green" | "amber" | "red" | "grey";

/** The memory watchdog light in the toolbar, plus the sentence shown in its tooltip and in Settings. */
export function memoryLight(s: RuntimeStatus | null): { light: Light; label: string; text: string } {
  if (!s) return { light: "grey", label: "…", text: "Waiting for the Mac…" };
  const gb = `${s.available_gb.toFixed(1)} GB free`;
  const label = `${Math.round(s.available_gb)} GB`;
  const downgraded = (s.event_id ?? 0) > 0 && /slower mode/i.test(s.event ?? "");
  let light: Light = "green";
  let text: string;
  switch (s.tier2_mode) {
    case "full": text = `Tutor fully in memory (${s.tier2_model}) · fast.`; break;
    case "external": text = `Tutor on an external model server (${s.tier2_model}).`; break;
    case "offload":
      light = "amber";
      text = downgraded ? "Memory low: tutor switched to slower mode." : `Tutor runs in slower mode (${s.detail || "experts paged from the SSD"}) to fit in memory.`;
      break;
    case "loading": light = "amber"; text = `Loading ${s.tier2_model}…`; break;
    case "refused": light = "red"; text = "Not enough memory: the local tutor couldn't start. Close other apps, or switch to Claude / OpenAI in Settings."; break;
    case "unavailable": light = "red"; text = `Local tutor unavailable: ${s.detail || "not started"}.`; break;
    default: light = "grey"; text = "Local tutor is off on this server.";
  }
  if (s.level === "critical") { light = "red"; text = `Memory very low (${gb}). ${text}`; }
  else if (s.level === "low") { if (light === "green") light = "amber"; text = `Memory getting low (${gb}). ${text}`; }
  else text = `${gb}. ${text}`;
  return { light, label, text };
}
