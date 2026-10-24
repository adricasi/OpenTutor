# 0002. Run OpenTutor as a local, offline hub

- Status: Proposed
- Date: 2026-10-24
- Deciders: OpenTutor team

## Context
Students have cheap phones and no mobile data (product.md). Users are minors, so no
personal data may leave their reach (G-3, NFR-002). The tutor must work where there is
no internet (NFR-003) and cost nothing to run (NFR-007). A donated laptop with 64 GB RAM
and a dedicated GPU is available as the reference machine.

## Options considered
1. **Local hub: one laptop runs model and web app, phones connect over its Wi-Fi** —
   Pros: zero data, zero cost, privacy by construction, works offline. Cons: one machine's
   GPU limits concurrency; setup on site; plain HTTP on the LAN.
2. **Cloud backend (GCP + hosted Gemma/Gemini)** — Pros: scales, no hardware on site.
   Cons: needs data on every phone, running cost, minors' data leaves the room. Defeats
   the product's purpose.
3. **On-device model on each phone** — Pros: no hub. Cons: cheap phones can't run a
   multimodal model at usable speed; needs an app install.

## Decision
We will deploy OpenTutor as a local hub (option 1): the deployment target is
**Local / on-device**, with the hub laptop as the only machine running code, because it
is the only option that meets zero data, zero cost and the privacy constraints together.

## Consequences
- Positive: no cloud services, IAM, Terraform or secrets to manage; privacy is structural.
- Negative / risks: concurrency bounded by one GPU → request queue (REQ-009) and the
  benchmark (REQ-014); plain HTTP on the LAN → accepted risk on a password-protected hub
  Wi-Fi (design.md, Security design); setup burden → setup/start commands (REQ-012).
- Follow-ups: ADR-0003 (web stack), ADR-0004 (model runtime), ADR-0005 (state).
