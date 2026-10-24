# 0005. Keep all session state in memory in a single web process

- Status: Proposed
- Date: 2026-10-24
- Deciders: OpenTutor team

## Context
Conversations and photos of minors must not be written to disk and must vanish at session
end, after 30 minutes idle and on restart (REQ-011, NFR-002). Scale is 15 sessions and
5 active students (NFR-006). The queue must be fair across all students (REQ-009).

## Options considered
1. **In-memory store in one async web process** — Pros: nothing ever reaches disk;
   deletion is trivial; one process owns the queue, so ordering is exact. Cons: state is
   lost on crash (acceptable: that is the privacy rule); no horizontal scaling.
2. **SQLite (as in the brief)** — Pros: survives restarts. Cons: writes minors' data to
   disk, contradicting REQ-011/NFR-002; deletion must be proven, including journal files.
3. **Redis** — Pros: shared state for several workers. Cons: extra service for no need at
   this scale.

## Decision
We will keep sessions, conversations, images, the queue and teacher insights in memory in
a single web-app process, because it makes the privacy rules true by construction and the
scale fits one process.

## Consequences
- Positive: no database, no migrations, no data-at-rest protection to build.
- Negative / risks: memory growth → caps on sessions, message size, image size and turns
  kept (design.md); one process → the web server runs with exactly one worker.
- Follow-ups: if saved progress is ever wanted, it needs a new ADR and a privacy review.
