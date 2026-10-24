# Ink Tutor — design kit

Design docs, model manifest, test fixtures and small tools for the app in `../tutor-app/`: a local-first tutor for work done by hand — maths, diagrams, languages, accounting, sudoku — on an iPad (Safari + Apple Pencil) or a Mac, that marks your page like a teacher and can talk. Runs on one Apple-silicon Mac; an optional toggle sends only the tutoring step to Claude or OpenAI.

Start with `docs/ARCHITECTURE.md`, then `docs/UX.md` and `docs/ROADMAP.md`. Reference: `docs/MODELS.md`, `docs/RECORDINGS.md`, `docs/SCREEN-TUTOR.md`, `docs/DECISIONS.md`.

## Layout

- `docs/` — architecture, UX rules, models and memory budget, recording schema, roadmap, decisions log, screen-tutor design.
- `models/` — `manifest.toml` (pinned Hugging Face repos and revisions) and `fetch.py` (download/verify into `models/weights/`, git-ignored).
- `fixtures/recordings/` — real and synthetic stroke recordings (`inktutor.recording/1`); `fixtures/annotations/` — their ground truth (sidecars).
- `tools/synthetic/generate.py` — deterministic synthetic recordings for modes without real ones (sudoku, accounting).
- `tools/sysmon.py` — one-line memory/CPU check, run before loading models.

## Commands

```bash
make models          # download the pinned MLX weights (LOCAL_MODEL + tier 1 + voice)
make models-verify   # check them against the pinned revisions
make synthetic       # regenerate the synthetic fixtures (make synthetic-check verifies them)
make sysmon          # memory/CPU check
```

The app's own commands (`make dev`, `make test`, `make lint`, `make judge`, `make demo`, `make ipad`) live in `../tutor-app/Makefile`.
