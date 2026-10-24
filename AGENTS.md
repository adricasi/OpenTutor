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
<!-- Filled by the `spec` skill from docs/spec/product.md (3–5 lines). -->

## Stack
- Python 3.12+, managed with `uv` (`src/` layout, `pyproject.toml`, `uv.lock`)
- pytest + pytest-cov, ruff (lint + format), mypy (strict), pip-audit

## Architecture
<!-- Filled by the `spec` skill: short summary + link to docs/spec/design.md and ADRs. -->

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
<!-- TODO: add project conventions agents must follow (naming, module boundaries, API style). -->

## Domain rules
<!-- Filled by the `spec` skill: invariants from docs/spec/requirements.md. -->

## Project kit
<!-- Filled by the `project-kit` skill: project-specific agents/skills and when to use them. -->

## Do not touch without asking
- `.github/workflows/` (CI and security gates)
- `.claude/settings.json`, `.agents/` (agent configuration)
- `infra/` state/backend configuration
