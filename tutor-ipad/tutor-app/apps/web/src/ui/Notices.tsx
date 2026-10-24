// Error states that never block the page: toasts (auto-dismiss, ×), the reconnect banner, the memory light.
import { useEffect, useState } from "react";
import type { RuntimeStatus } from "../protocol";
import type { ConnInfo, Toast } from "../session";
import { memoryLight } from "./wording";
import "./notices.css";

const TOAST_MS: Record<Toast["kind"], number> = { info: 3500, warn: 7000, error: 9000 };

export function Toasts({ toasts, onDismiss }: { toasts: Toast[]; onDismiss: (id: number) => void }) {
  if (toasts.length === 0) return null;
  return (
    <div className="toasts" aria-live="polite">
      {toasts.map((t) => <ToastItem key={t.id} toast={t} onDismiss={onDismiss} />)}
    </div>
  );
}

function ToastItem({ toast, onDismiss }: { toast: Toast; onDismiss: (id: number) => void }) {
  useEffect(() => {
    const t = setTimeout(() => onDismiss(toast.id), TOAST_MS[toast.kind]);
    return () => clearTimeout(t);
  }, [toast.id, toast.kind, onDismiss]);
  return (
    <div className={`toast ${toast.kind}`} role={toast.kind === "error" ? "alert" : "status"} data-testid="toast">
      <div className="toast-body">
        <strong>{toast.title}</strong>
        {toast.text && <span>{toast.text}</span>}
      </div>
      <button className="toast-x" aria-label="Dismiss" onClick={() => onDismiss(toast.id)}>×</button>
    </div>
  );
}

/** Shown while the WebSocket is down (after it had connected, or after a failed first try). Ink is queued meanwhile. */
export function ConnectionBanner({ connected, conn, onRetry }: { connected: boolean; conn: ConnInfo; onRetry: () => void }) {
  const [dismissed, setDismissed] = useState(-1); // the outage the user dismissed
  if (connected || (!conn.everConnected && conn.attempt < 2) || dismissed === conn.outage) return null;
  const secs = conn.retryInMs != null ? Math.max(1, Math.round(conn.retryInMs / 1000)) : null;
  return (
    <div className="conn-banner" role="status" data-testid="conn-banner">
      <span>
        {conn.everConnected ? "Connection to the Mac lost" : "Can't reach the Mac"} — reconnecting
        {secs != null ? ` in ${secs} s` : "…"}{conn.attempt > 1 ? ` (attempt ${conn.attempt})` : ""}.
        {" "}Keep writing: your ink is sent when it's back.
        {conn.attempt >= 5 ? " Is the Mac awake and on the same Wi-Fi?" : ""}
      </span>
      <button onClick={onRetry}>Retry now</button>
      <button className="toast-x" aria-label="Hide" onClick={() => setDismissed(conn.outage)}>×</button>
    </div>
  );
}

/** Compact traffic light in the toolbar: free memory and how the local tutor runs. Tap → Settings. */
export function MemoryIndicator({ status, onClick }: { status: RuntimeStatus | null; onClick: () => void }) {
  const m = memoryLight(status);
  return (
    <button className={`mem-light ${m.light}`} onClick={onClick} title={m.text} aria-label={`Memory: ${m.text}`} data-testid="mem-light">
      <span className="mem-dot" />{m.label}
    </button>
  );
}

/** The details in Settings. */
export function MemoryDetails({ status }: { status: RuntimeStatus | null }) {
  const m = memoryLight(status);
  return (
    <div className="mem-details" data-testid="mem-details">
      <p><span className={`mem-dot ${m.light}`} /> {m.text}</p>
      {status && (
        <p className="muted">
          {status.available_gb.toFixed(1)} GB free · local tutor {status.tier2_model} · {status.tier2_mode}
          {status.detail && status.detail !== status.tier2_mode ? ` (${status.detail})` : ""}
        </p>
      )}
      {status?.event && <p className="muted">Last event: {status.event}</p>}
    </div>
  );
}
