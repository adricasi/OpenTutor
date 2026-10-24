// "Pair iPad" (start screen): the HTTPS tunnel QR from `make pair` (the microphone needs HTTPS in Safari)
// and the plain-http LAN URLs. Polls GET /api/pairing so the QR appears as soon as the tunnel is up.
import { useEffect, useState } from "react";
import "./pairing.css";

export interface PairingInfo {
  tunnel: { url: string; qr: string; reachable: boolean } | null;
  lan: { url: string; mdns: string; qr: string; note: string };
  bonjour: string | null;
  hint: string | null;
}

export function PairingPage({ onClose, pollMs = 3000 }: { onClose: () => void; pollMs?: number }) {
  const [info, setInfo] = useState<PairingInfo | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    const load = async () => {
      try {
        const r = await fetch("/api/pairing");
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const j = (await r.json()) as PairingInfo;
        if (live) { setInfo(j); setError(null); }
      } catch (e) {
        if (live) setError(`Can't reach the server (${(e as Error).message}).`);
      }
    };
    void load();
    const t = setInterval(() => void load(), pollMs);
    return () => { live = false; clearInterval(t); };
  }, [pollMs]);

  return (
    <div className="pairing-backdrop" onClick={onClose}>
      <div className="pairing card" role="dialog" aria-label="Pair iPad" onClick={(e) => e.stopPropagation()}>
        <header>
          <h2>Pair iPad</h2>
          <button type="button" onClick={onClose} aria-label="Close">Close</button>
        </header>
        {error && <p className="pairing-error">{error}</p>}
        {!info && !error && <p className="muted">Loading…</p>}
        {info && (
          <>
            <section className="pairing-main" data-testid="pairing-tunnel">
              {info.tunnel ? (
                <>
                  <img src={info.tunnel.qr} alt="QR code for the HTTPS link" width={220} height={220} />
                  <div>
                    <h3>Scan with the iPad camera</h3>
                    <p className="pairing-url"><a href={info.tunnel.url}>{info.tunnel.url}</a></p>
                    <p className="muted">
                      HTTPS through a Cloudflare tunnel, so the microphone works.
                      {info.tunnel.reachable ? "" : " The link may need a few more seconds to come up."}
                      {" "}The code in the link keeps it private.
                    </p>
                  </div>
                </>
              ) : (
                <div>
                  <h3>No HTTPS link yet</h3>
                  <p className="muted">Run <code>make pair</code> on the Mac; its QR code appears here.
                    Safari only allows the microphone over HTTPS.</p>
                </div>
              )}
            </section>
            <section className="pairing-lan" data-testid="pairing-lan">
              <img src={info.lan.qr} alt="QR code for the LAN link" width={120} height={120} />
              <div>
                <h3>Same Wi-Fi (no microphone)</h3>
                <p className="pairing-url"><a href={info.lan.url}>{info.lan.url}</a> · <a href={info.lan.mdns}>{info.lan.mdns}</a></p>
                <p className="muted">{info.lan.note}</p>
                {info.bonjour && <p className="muted">Advertised over Bonjour as “{info.bonjour}”.</p>}
              </div>
            </section>
          </>
        )}
      </div>
    </div>
  );
}

/** The start screen's entry point: a link-style button that opens the dialog. */
export function PairButton() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" className="pair-link" onClick={() => setOpen(true)}>Pair iPad</button>
      {open && <PairingPage onClose={() => setOpen(false)} />}
    </>
  );
}
