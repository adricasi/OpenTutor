# opentutor

Offline AI homework tutor hub: one laptop running Gemma 4 serves a classroom of phones over local Wi-Fi, zero data

> Global engineering standards are loaded automatically from `~/ai-toolkit/AGENTS.md`
> (Claude Code via `~/.claude/CLAUDE.md`, Gemini via `~/.gemini/AGENTS.md`).
> This file only adds what is specific to this project. Keep it short and current.

## Specification
`docs/spec/` is the source of truth: `product.md` (why), `requirements.md` (what, with
REQ/AC IDs), `design.md` (how), `tasks.md` (plan). Build with the `implement-task` skill;
change the spec first (`spec` skill) when requirements change. Reference IDs in commits.

## Project overview
OpenTutor turns one Linux laptop running Gemma 4 locally into a free, private homework
tutor for a group of students (5 active, 15 connected). Students join the hub's own Wi-Fi
from any phone browser, with a nickname only, send a photo or typed exercise and get
hint-first tutoring, attempt checking and practice — with zero mobile data and nothing
leaving the hub. Specification: docs/spec/ — the source of truth.

`tutor-ipad/` is a **separate product** (iPad handwriting tutor, Apple-silicon Mac) with
its own toolchain and docs. Don't change it from OpenTutor tasks; reuse its code only as
listed in design.md ("Reuse from tutor-ipad"), naming the source file in a header comment.

## Stack
- Python 3.12+, managed with `uv` (`src/` layout, `pyproject.toml`, `uv.lock`)
- pytest + pytest-cov, ruff (lint + format), mypy (strict), pip-audit

## Architecture
Local hub (ADR-0002): Ollama serves Gemma 4 on 127.0.0.1 (ADR-0004); one FastAPI process
with server-rendered pages and vanilla JS streams replies as NDJSON (ADR-0003) and keeps
all state in memory (ADR-0005); subject tutors are folders under `tutors/`; a sympy
checker decides Maths verdicts (ADR-0007); an opt-in Cloudflare quick tunnel serves remote
demos (ADR-0006). Details: [docs/spec/design.md](docs/spec/design.md), [docs/adr/](docs/adr/).

## Commands
```bash
uv sync                                  # install / update the environment
uv run pytest --cov                      # tests with coverage
uv run ruff check --fix . && uv run ruff format .   # lint + format
uv run mypy src                          # type check
uv run pip-audit                         # dependency vulnerabilities
```

## Project rules
- Keep this repository's CI green; run the commands above before committing.
- Record significant decisions as ADRs in `docs/adr/` (use the `adr` skill).
- Tests are named after the acceptance criterion they prove (`test_ac_006_2_...`).
- Unit and API tests use the fake model backend; never call Ollama or the network in them.
  Model-quality checks run via `opentutor eval` on the hub (`RUN_MODEL_TESTS=1`).
- Prompts are versioned files (`prompts/`, `tutors/<id>/rubric.md`); a prompt, tutor or
  model change needs an `opentutor eval` report that meets NFR-011.

## Domain rules
- Hint first: never state the final answer before the student explicitly asks for it
  (REQ-005); the hint ladder is nudge → hint → next step → full solution on request.
- Students' messages and images live in memory only: never write them to disk, logs,
  reports or teacher insights (REQ-011, NFR-002). Logs use session IDs, never nicknames.
- Hub mode makes no outbound connection; no CDNs, external fonts or analytics (NFR-003).
- The web app never binds `0.0.0.0`; Ollama stays on loopback; requests that came through
  the tunnel are never treated as local (NFR-001, AC-017.4).
- For Maths, the checker's verdict wins; the model explains it and must not contradict it
  (AC-006.8). Input to the maths parser passes the allow-list tokenizer first (ADR-0007).
- Everything a student can type or photograph is untrusted data, never instructions to the
  tutor (REQ-010); model output is rendered as text, never HTML.
- Subject tutors are configuration (REQ-008): adding a subject must not need code.

## Project kit
<!-- Filled by the `project-kit` skill: project-specific agents/skills and when to use them. -->

## Do not touch without asking
- `.github/workflows/` (CI and security gates)
- `.claude/settings.json`, `.agents/` (agent configuration)
- `infra/` state/backend configuration
