# 0006. Offer an optional online demo link through a Cloudflare quick tunnel

- Status: Proposed
- Date: 2026-10-24
- Deciders: OpenTutor team

## Context
The hub serves students over its own Wi-Fi with no internet (ADR-0002). For demos
(hackathon judges, remote testers) people may not be able to join the hub Wi-Fi.
`tutor-ipad` (same repository) already reaches its server through a token-gated
Cloudflare quick tunnel (`tutor-ipad/tutor-app/apps/server/src/inktutor_server/pairing.py`,
`pair.py`; its DECISIONS #11, #22, #26). Cloudflare documents quick tunnels as for testing
and development: no uptime guarantee, a 200 in-flight request limit, no Server-Sent Events.

## Options considered
1. **Tunnel as the normal way in** — Pros: works on any network, HTTPS. Cons: phones and
   hub need internet (defeats zero data), minors' content passes Cloudflare's edge
   (TLS ends there), testing-only service. Rejected.
2. **Optional quick tunnel, off by default, token-gated** — Pros: easy demos from
   anywhere; reuses tested code. Cons: one more mode to secure and explain.
3. **Named Cloudflare tunnel or ngrok** — Pros: stable URL. Cons: account and domain
   (Cloudflare) or account and limits (ngrok); more than a demo needs.
4. **No remote access** — Pros: simplest. Cons: weaker demos when the venue blocks the hub.

## Decision
We will offer an **opt-in online demo link** (option 2, REQ-017): `opentutor start
--online-demo` opens a Cloudflare quick tunnel to the web app, gated by an access token
carried in the QR code and then kept in an HttpOnly cookie, adapted from `tutor-ipad`'s
`QuickTunnel` and `TunnelGate`. Hub mode stays the default and makes no outbound
connection.

## Consequences
- Positive: demos work on venue networks; the code is already tested in `tutor-ipad`.
- Negative / risks:
  - Tunnel traffic reaches the app from `127.0.0.1`, so "loopback = local" is false in
    this mode → requests carrying Cloudflare headers (`cf-connecting-ip`, `cf-ray`) or a
    `*.trycloudflare.com` host are always treated as remote (AC-017.4).
  - Content passes Cloudflare → start-up warning and on-page notice (AC-017.2, AC-017.5);
    NFR-002/NFR-003 apply to hub mode only.
  - Streaming through the tunnel must be tested (NDJSON over `fetch`, not SSE).
  - `cloudflared` is a downloaded binary → pinned version and SHA-256 check, Linux
    asset added (the `tutor-ipad` downloader is macOS-only); downloaded during setup,
    never at start.
- Follow-ups: T-021.
