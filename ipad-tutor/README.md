# ipad-tutor — handwriting tutor for the iPad

A local-first tutor for work done by hand. The student writes on an iPad (Apple Pencil, Safari) or on the Mac; the
tutor reads the handwriting, checks it with deterministic checkers, and marks the page like a teacher: small marks
anchored to the strokes, hints on request (nudge → hint → corrected step → full solution only when asked), spoken
answers to spoken questions, and a teacher view with homework links and a short progress summary per student.
Everything runs on one Apple-silicon Mac with open-weights models; a per-session toggle can send only the tutoring
step to a cloud provider.

This folder is self-contained. **Integrate it as described below; don't restructure it.**

## What the host repository must do

1. **Keep this folder as is** (`ipad-tutor/`) and run it as its own service. It is a FastAPI server that also serves
   the built web app; nothing in it imports from the host repo, and nothing in the host repo needs to import it.
2. **Provide the models through `ink-tutor/models/manifest.toml`**, the single place the app looks for them. If the
   host repo keeps models elsewhere, point the app at it — don't change the code:
   - `SPEC_DIR=/path/to/a/folder` that contains `models/manifest.toml` and `fixtures/` (default: `./ink-tutor`).
   - Each manifest entry pins an exact Hugging Face repo + revision; `make models` downloads them into
     `SPEC_DIR/models/weights/` (git-ignored). Keep the keys (`tier1.text`, `tier1.photo`, `tier2.<name>`,
     `voice.vad`, `voice.stt`, `voice.tts`); swap repos/revisions if you must.
3. **Tier-2 tutor model:** by default the app starts and manages an `mlx_vlm.server` itself (`LOCAL_SERVER_MANAGED=true`).
   If the host repo already serves a model, set `LOCAL_SERVER_MANAGED=false` and
   `LOCAL_OPENAI_BASE_URL=http://host:port/v1`: any **OpenAI-compatible `/chat/completions` endpoint with streaming and
   image input** works. Default model: Gemma 4 26B-A4B (`LOCAL_MODEL=gemma4-26b-a4b`).
4. **Recognition (tier 1) stays local and in-process** (Qwen3.5-4B via mlx-vlm, loaded on the first ink). It is never
   sent to a cloud provider — keep it that way.
5. **Ports:** the app listens on `:8000` (set in `tutor-app/Makefile`); the managed model server on `:8080`
   (`LOCAL_OPENAI_BASE_URL`). Keep them free.
6. **Secrets** only in `tutor-app/.env` (copy `.env.example`); the browser never sees keys.
7. **CI:** run this folder's own checks from `tutor-app/` (`make test`, `make lint`). Its lint settings are in
   `ruff.toml` and `tutor-app/pyproject.toml`; the host repo's root linters should exclude or respect them.

## Requirements

- macOS on Apple silicon (MLX), 32 GB+ RAM recommended (the tutor model is ~16 GB resident; reader ~3.6 GB; voice ~3 GB).
- Python 3.12 + [uv](https://docs.astral.sh/uv/), Node 20+, ~45 GB disk for the models.
- Optional: `espeak-ng` (Homebrew) for better text-to-speech of unusual words and non-English voices.

## Run

```sh
./setup-local.sh                 # repo-local uv/npm caches (writes uv.toml and tutor-app/apps/web/.npmrc)
cd tutor-app
make models                      # download the pinned models (manifest)
make ipad                        # build web, start server + HTTPS tunnel, print a QR code for the iPad
```

- `make server` — server only (LAN: `http://<mac>.local:8000`; the iPad microphone needs HTTPS, so use `make ipad`/`make pair`).
- `make demo` — replays a recorded session into the canvas so the tutor reacts live.
- `/teacher` (on the Mac or LAN) — create homework, share the link/QR, see who did it and a 2–5 sentence summary.
- `make test`, `make lint`, `make e2e` — server tests, web tests, browser tests (Playwright, Chromium).

## Layout

```
tutor-app/
  apps/server/      FastAPI: ink geometry, recognition (tier 1), checkers, agent, tutor (tier 2), voice, teacher view
  apps/web/         React canvas (Pencil input, marks, hints, voice, teacher pages)
  packages/protocol pydantic models → schema.json → TypeScript types (the one contract)
  modes/            one folder per subject: math, consulting, system_design, language, accounting, sudoku
ink-tutor/
  models/           manifest.toml (pinned models) + fetch.py
  fixtures/         recorded sessions + annotations used by the tests
  tools/            synthetic fixture generator, memory monitor
```

## How it works (short)

Pen strokes stream over a WebSocket (`/ws/session/{id}`) → geometry groups them into lines → the tier-1 reader turns
each line into LaTeX/text with a confidence (low confidence → a "did you mean…?" chip, not a grade) → deterministic
checkers (sympy equivalence, debits = credits, sudoku rules, graph rules) decide right/wrong → the tier-2 tutor only
explains, hints and speaks; it never overrides a checker. Voice: Silero VAD → Parakeet speech-to-text → the tutor →
Kokoro text-to-speech, streamed sentence by sentence. The student's ink is never modified; the tutor draws on its own
layer.
