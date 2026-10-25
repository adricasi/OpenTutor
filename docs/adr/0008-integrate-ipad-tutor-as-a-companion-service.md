# 0008. Integrate the iPad handwriting tutor as a companion service

- Status: Proposed
- Date: 2026-10-24
- Deciders: OpenTutor team

## Context
The team merged `ipad-tutor/` (#18, formerly `tutor-ipad/`): a handwriting tutor for the iPad
that reads Apple Pencil strokes on an Apple-silicon Mac (MLX models), checks the work with
deterministic checkers and marks the page. Its README asks the host repository to keep the
folder as is, run it as its own service, give it models through its manifest, keep its ports
(8000 app, 8080 model server) free, and run its own checks. The phone hub (ADR-0002) runs on a
Linux laptop with Ollama, serves plain pages on 8000 and keeps all student data in memory.

## Options considered
1. **Companion service, integrated at the repository and operations level** — keep both apps
   separate; the repo provides lint exclusion, docs, a port plan and an optional shared model
   endpoint. Pros: no rewrite; each keeps its tested design and data rules. Cons: two apps to
   run and explain.
2. **Merge the iPad tutor into the hub** — one server. Cons: MLX and the ink pipeline only run
   on Apple silicon while the hub targets Linux; very different stacks (React canvas,
   WebSocket ink protocol, SQLite store vs in-memory); hours of rework before a deadline.
3. **Keep it unrelated** — Cons: the repository presents two products with no story or
   guidance, and the root checks break on its code.

## Decision
We will integrate `ipad-tutor/` as a **companion service** (option 1): unchanged folder, its
own service and checks, root linters excluding it, a README section and port plan for running
both on one Mac (hub on another port, e.g. 8100), and an optional shared Gemma 4 through the
hub's Ollama OpenAI-compatible endpoint (`LOCAL_SERVER_MANAGED=false`,
`LOCAL_OPENAI_BASE_URL=http://127.0.0.1:11434/v1`).

## Consequences
- Positive: both front ends ship; nothing in the hub depends on Apple hardware.
- Negative / risks:
  - Data rules differ: the iPad tutor keeps sessions in its own store and offers an optional
    cloud tutoring toggle; the hub's NFR-002/NFR-003 apply to the hub only. Operators read
    each README.
  - The shared Ollama endpoint is untested; the iPad tutor uses the first model Ollama lists.
  - CI: its checks need their own job (a workflow change, pending approval).
- Follow-ups: test the shared endpoint on a Mac; add the CI job.
- Resolved follow-up (REQ-021, T-029): the hub links to the iPad tutor at `/ipad` via
  `OPENTUTOR_IPAD_URL` (a redirect only; without it `/ipad` explains how to connect it). The
  join and subjects pages show the iPad entry; the services stay separate.
