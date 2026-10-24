// Provider toggle (local / Claude / OpenAI), the local model picker (manifest-driven, one resident at a time),
// the memory watchdog details, per-session options and the intro.
import { useEffect, useState } from "react";
import type { ClientMessage, RuntimeStatus, Session, TutorInfo } from "../protocol";
import { MemoryDetails } from "./Notices";

interface LocalModel { key: string; name: string; memory_gb: number; downloaded: boolean; loaded: boolean }
interface ServerSettings {
  provider: string;
  local_model: string;
  local_models: LocalModel[];
  cloud: { anthropic: boolean; openai: boolean };
  loading: string | null;
}

export function Settings({ session, tier2, send, onClose, runtime = null, onShowIntro }: {
  session: Session;
  tier2: TutorInfo | null;
  send: (m: ClientMessage) => void;
  onClose: () => void;
  runtime?: RuntimeStatus | null;
  onShowIntro?: () => void;
}) {
  const [s, setS] = useState<ServerSettings | null>(null);
  const [provider, setProvider] = useState(session.provider ?? "local");
  const [keepHistory, setKeepHistory] = useState(session.keep_history ?? false);
  const [busy, setBusy] = useState("");

  useEffect(() => {
    let alive = true;
    const load = () => fetch("/api/settings").then((r) => (r.ok ? r.json() : null)).then((j) => alive && setS(j)).catch(() => {});
    load();
    const t = setInterval(load, 3000); // shows "loading <model>" progress during a swap
    return () => { alive = false; clearInterval(t); };
  }, []);

  const pickModel = async (key: string) => {
    setBusy(`loading ${key}…`);
    const r = await fetch("/api/settings", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ local_model: key }) });
    const j = await r.json().catch(() => ({}));
    setBusy(r.ok ? "" : j.detail ?? `HTTP ${r.status}`);
  };

  return (
    <div className="settings card" role="dialog">
      <button className="close" aria-label="Close" onClick={onClose}>×</button>
      <h3>Tutor</h3>
      <div className="seg">
        {(["local", "anthropic", "openai"] as const).map((p) => (
          <button key={p} className={provider === p ? "on" : ""}
            disabled={p !== "local" && s ? !s.cloud[p] : false}
            title={p !== "local" && s && !s.cloud[p] ? "No API key in .env" : ""}
            onClick={() => { setProvider(p); send({ type: "session.update", provider: p }); }}>
            {p === "local" ? "Local" : p === "anthropic" ? "Claude" : "OpenAI"}
          </button>
        ))}
      </div>
      <p className="muted">Only the tutoring step goes to the cloud (crops of what changed + the reading). Handwriting recognition always stays on this Mac.</p>
      <h3>Local model</h3>
      {!s && <p className="muted">{tier2 ? `${tier2.model}${tier2.ready ? "" : " — " + (tier2.detail || "not ready")}` : "…"}</p>}
      {s?.local_models.map((m) => (
        <label key={m.key} className="model">
          <input type="radio" name="model" checked={s.local_model === m.key} disabled={!m.downloaded || !!s.loading}
            onChange={() => void pickModel(m.key)} />
          <span>{m.name}</span>
          <span className="muted">{m.memory_gb} GB{m.loaded ? " · loaded" : m.downloaded ? "" : " · not downloaded"}</span>
        </label>
      ))}
      {(s?.loading || busy) && <p className="muted">{s?.loading ? `loading ${s.loading}…` : busy}</p>}
      <h3>Memory</h3>
      <MemoryDetails status={runtime} />
      <h3>This session</h3>
      <label className="check">
        <input type="checkbox" checked={keepHistory} data-testid="keep-history"
          onChange={(e) => { setKeepHistory(e.target.checked); send({ type: "session.update", keep_history: e.target.checked }); }} />
        <span>Keep page history after wipe
          <span className="muted"> — when you wipe or erase the page, the tutor keeps a short summary of what was on it (what it read, and whether it was right) as context. Off: it forgets erased work.</span>
        </span>
      </label>
      {typeof window !== "undefined" && !window.isSecureContext && (
        <p className="muted">Voice needs the microphone, which Safari only allows on HTTPS pages: on the iPad open the https:// address.</p>
      )}
      {onShowIntro && <button className="secondary" onClick={onShowIntro}>Show the intro again</button>}
    </div>
  );
}
