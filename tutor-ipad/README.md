# Open Tutor — iPad canvas tutor

A local-first tutor for work done by hand. You write on an iPad (Apple Pencil, Safari) or on the Mac; the tutor reads
the handwriting, checks it with deterministic checkers and marks the page the way a teacher marks paper: small marks
anchored to your strokes, hints when you ask, never the full solution unprompted. It can listen and talk.

Everything runs on one Apple-silicon Mac with MLX models. A per-session toggle can send only the tutoring step (never
the handwriting recognition) to a cloud provider.

A second mode, the **screen tutor**, is designed but not built yet: on the Mac it walks you through software tasks
in any app with highlights, arrows and spoken steps, and never clicks for you (`ink-tutor/docs/SCREEN-TUTOR.md`).

## Highlights

- **Your ink stays yours.** Marks (✓ ✗ ?, underlines, pins) and the tutor's "ghost ink" sit on a separate layer,
  anchored to line ids, stroke ids or boxes. Nothing you wrote is redrawn or typeset.
- **Deterministic checkers first.** Maths step equivalence with sympy, accounting (debits = credits, T-account
  totals and balances), sudoku rules, and diagram rules (orphans, single points of failure). The language model
  explains and gives hints; it cannot overrule a checker.
- **Hint ladder.** Nudge → hint → corrected step → full solution, the last only on an explicit request.
- **Fast first feedback.** A line is checked once it settles; the first mark lands in under 4 s on the reference
  Mac (median 2.6 s on the maths fixture, `ink-tutor/docs/MODELS.md`).
- **Voice.** Local VAD, speech-to-text and text-to-speech (Silero, Parakeet, Kokoro); "hint" / "more" voice commands
  are answered in about 1 s, and spoken questions start being answered in about 2 s with the local model warm.
- **Local models, optional cloud.** Gemma 4 26B-A4B is the default local tutor, Qwen models are selectable in the
  settings; Claude or OpenAI can be switched on per session for the tutoring step, with a badge showing who answered.
- **iPad over HTTPS.** `make ipad` starts the server and a token-gated Cloudflare quick tunnel and prints a QR code,
  so it works on networks you don't control and Safari allows the microphone. Plain LAN http also works.
- **Subjects as plugins.** Maths, system design, consulting, language, accounting and sudoku are folders under
  `tutor-app/modes/` (config + rubric).
- **Planned:** the screen tutor, and a teacher view.

## Requirements

- An Apple-silicon Mac on macOS 14 or later. The reference machine is an M3 Pro with 36 GB; the design budget is
  32 GB of unified memory (the runtime checks free memory before loading a model and falls back to a slower
  expert-offload mode when it is tight).
- Python 3.12 managed by [uv](https://docs.astral.sh/uv/), and Node 20+ with npm.
- Disk: about 50 GB for the default model set, about 70 GB if you download every registered model
  (`ink-tutor/models/manifest.toml`).
- Optional: `espeak-ng` (Homebrew) for non-English voices; without it speech output is English-only.
  `cloudflared` for the iPad tunnel is downloaded automatically (pinned, checksum-verified) into `.tools/`.
- Optional: an `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` in `tutor-app/.env` for the cloud toggle (see `.env.example`).

All caches (uv, npm, Hugging Face, Playwright) stay inside this folder.

## Quick start

```sh
./setup-local.sh                 # writes uv.toml / .npmrc so caches stay in this folder
cd tutor-app
make models                      # download the pinned MLX models
make ipad                        # server + HTTPS tunnel + QR code to open on the iPad
make demo                        # or: replay a recorded maths session into the canvas, live
```

`make dev` runs the server and the Vite dev server; `make test`, `make lint` and `make e2e` run the checks
(see `tutor-app/Makefile`). Model-dependent tests are skipped by default.

## Layout

```
tutor-app/                 the app
  apps/server/             FastAPI server: ink store, recognition, checkers, tutor providers, agent, voice, runtime
  apps/web/                React + TypeScript canvas (ink layer, tutor layer, popovers, voice dock)
  packages/protocol/       pydantic models → JSON schema → generated TypeScript types
  modes/                   one folder per subject (config.toml + rubric.md)
ink-tutor/                 design kit
  docs/                    architecture, UX, models, recordings, roadmap, decisions, screen tutor
  models/                  pinned model manifest + downloader
  fixtures/                recorded and synthetic stroke sessions + ground-truth annotations (test data)
  tools/                   synthetic fixture generator, memory/CPU check
```

## Docs

- [Architecture](ink-tutor/docs/ARCHITECTURE.md) — components, protocol, agent loop, networking
- [UX](ink-tutor/docs/UX.md) — how the tutor marks, hints and talks
- [Models](ink-tutor/docs/MODELS.md) — model choices, memory budget, measurements
- [Recordings](ink-tutor/docs/RECORDINGS.md) — fixture schema and how tests use it
- [Roadmap](ink-tutor/docs/ROADMAP.md) · [Decisions](ink-tutor/docs/DECISIONS.md) · [Screen tutor](ink-tutor/docs/SCREEN-TUTOR.md)
