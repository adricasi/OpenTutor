# Roadmap and acceptance criteria

Work phase by phase. Each phase ends with its tests green. Do not start a later phase's features early; stubs are fine.

## Phase 0 — Fixtures exist ✅ tooling ready
- A separate stroke-recorder tool captures sessions into `fixtures/recordings/` (schema in `RECORDINGS.md`).
- Record 5–10 sessions across math, diagram, language (and accounting if time).
- Done when: ≥ 5 JSON files with at least one `final` checkpoint each and `truth` filled.

## Phase 1 — Protocol + server skeleton
- `packages/protocol`: pydantic models from ARCHITECTURE §2, JSON schema export, TS generation (`datamodel-codegen` or `pydantic2ts`), snapshot test.
- `apps/server`: FastAPI app, `ws/session/{id}` echoing `ink.update` as `status`, SQLite store, `make dev`.
- Fixture loader: `fixtures.load(path) -> Recording`, replay iterator with timing.
- Done when: a replay test streams a fixture's strokes into the WebSocket and the store holds them.

## Phase 2 — Canvas with ink/tutor layers
- `apps/web`: tldraw canvas, ink layer with pressure, palm rejection toggle, dirty-region tracker, debounce, `ink.update` sender, `reading`/`feedback` renderer with `MarkShape`, `GhostInkShape`, `ConfidenceChip`, popover with hint ladder UI (static content for now), provider badge, debug pane.
- PWA manifest; works in iPad Safari over the LAN.
- Done when: Playwright drives a fixture into the canvas via pointer events and the server receives equivalent strokes (same count, points within 1 px).

## Phase 3 — Math mode end to end (local)
- `ink/`: rasterizer, line grouping. `recognize/MathRecognizer` with mathnote-ocr (incremental, stroke→symbol map, confidence, alternatives).
- `checkers/math.equivalent` via sympy.
- `tutor/LocalMLX` against mlx-vlm, parameterised by `LOCAL_MODEL` from `models/manifest.toml`; `runtime/` can start/swap the model server; `agent` state machine; prompts in `tutor/prompts/math.md`.
- `make judge MODEL=<key>` runs the fixture judge for a given local model so Qwen3.6-35B-A3B and Gemma 4 26B-A4B can be compared on the real recordings.
- Done when: math fixtures pass tier-1 thresholds, `mistake` checkpoints get ✗ + underline on the right strokes, `step` checkpoints get ✓, first mark < 4 s on the reference Mac (M3 Pro, 36 GB), new ink cancels in-flight review.

## Phase 4 — Hints, questions, ghost ink
- Hint ladder (4 levels, cached per line), confidence-gate questions, ghost ink accept/dismiss/retire-on-fix.
- Done when: the LLM judge passes ≥ 80% of math `expect`s; no `hint_level 4` without an explicit request (test).

## Phase 5 — Provider toggle
- `tutor/Anthropic`, `tutor/OpenAI` behind the same interface; per-session provider switch plus local-model picker (manifest-driven, runtime swap with memory check); badge with provider, model and latency; automatic local fallback on cloud failure; `.env` keys only server-side.
- Done when: the same fixture produces structurally valid feedback from all three providers; the UI shows which one answered.

## Phase 6 — Diagram mode
- `ink/` diagram pre-pass; `recognize/DiagramRecognizer` → `Graph` with bboxes; `checkers/graph.rules`; `modes/system_design` and `modes/consulting` rubrics; numbered pins + checklist; ghost boxes/arrows; "show reference design" pane.
- Done when: diagram fixtures' node/edge `truth` matches ≥ 85%; judge passes ≥ 75% of `expect`s.

## Phase 7 — Language mode
- `recognize/TextRecognizer` (Qwen3.5-4B, bboxes); `modes/language` with per-language rubric; squiggles, ghost corrections; CJK stroke-order playback (data source TBD).
- Done when: text CER ≤ 10% on fixtures; judge ≥ 75%.

## Phase 8 — Photos
- `POST /photos`, Chandra on demand with memory watchdog, bbox anchors on a photo background.
- Done when: photo fixtures' `truth` match; Chandra is unloaded after each job (test via runtime API).

## Phase 9 — Voice
- `voice/`: Silero VAD, Parakeet streaming STT, Kokoro TTS, `Tutor.converse`, barge-in, pen-anchor resolution, hint-ladder voice commands; cloud realtime when provider ≠ local.
- The stroke recorder gains a mic button so future fixtures include speech + timing.
- Done when: speech end → first audio < 1.5 s local; barge-in stops playback within 200 ms.

## Phase 10 — Polish for the demo
- Bonjour + QR pairing page, accounting and sudoku modes, memory watchdog UI, onboarding, error states, `make demo` that loads a scripted session.

## Screen tutor phases (`docs/SCREEN-TUTOR.md`)
These can run in parallel with the canvas phases once the provider toggle exists (Phase 5, done); the overlay helper does not depend on the canvas.

## Phase 11 — Overlay helper
- `tutor-app/apps/overlay` (Swift/AppKit, SwiftPM, ad-hoc signed): transparent click-through window across displays drawing `OverlayEvent`s from the WebSocket; draggable phantom icon + menu; Screen Recording + Accessibility permissions; `make overlay`.
- Protocol: the screen types and unions in `packages/protocol`; `/ws/screen/{id}` (localhost only).
- Done when: a server-sent highlight + caption appears over Excel at the right place on two displays.

## Phase 12 — Perception
- Helper: ScreenCaptureKit capture (~1 fps + on click + on focus change, downscaled), AX snapshot reader, input event hooks.
- Server: `screen/` frame diff, `ax/` search/predicates; secure-field/blocklist pausing (`SCREEN_BLOCKLIST`); `ink-tutor/tools/screen-recorder` writing `fixtures/screens/`.
- Done when: AX finds Excel's "Insert" by role+title in < 100 ms and a pivot-table walkthrough fixture exists.

## Phase 13 — Guide loop
- `guide/`: planner (goal → plan, `Tutor.plan`), stepper, locating (AX first, VLM bbox fallback via `Tutor.locate`), `done_when` evaluation, replanning, hint ladder; `modes/screen`, `modes/apps/excel.md` recipe; voice commands next/back/repeat/stop in `voice/session.py`.
- Done when: the pivot-table fixture replays with ≥ 90% targets resolved by AX and auto-advance within 1 s of each recorded completion; a live run works with voice.

## Always
- Keep `docs/DECISIONS.md` updated when a decision changes.
- Every new mode ships with at least one fixture and a replay test.
