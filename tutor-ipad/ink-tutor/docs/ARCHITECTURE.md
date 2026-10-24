# Architecture

Status: design reference; where the app as built differs (e.g. the hand-rolled canvas instead of tldraw), `DECISIONS.md` records it. Companion docs: `UX.md` (what the user sees), `MODELS.md` (which models, memory), `RECORDINGS.md` (fixture format), `ROADMAP.md` (build order), `DECISIONS.md` (why).

## 0. Non-negotiables

The design rules every part of the app follows; code comments and DECISIONS refer to them by number.

1. **Ink stays ink.** The user's strokes are never modified, snapped, typeset or redrawn. The tutor adds marks and ghost ink on separate layers; the user can erase or dismiss them.
2. **Feedback is anchored.** Every tutor output references a line id, stroke ids or a bbox. No floating paragraphs on the canvas; long explanations live in popovers / a side panel.
3. **Two tiers.** Tier 1 (recognition) is instant and always local. Tier 2 (tutoring) is a local VLM by default, swappable per session to Anthropic or OpenAI behind one `Tutor` interface. The local model is a config var (`LOCAL_MODEL`) and a UI picker backed by `models/manifest.toml` (Gemma 4 26B-A4B default, DECISIONS #25; Qwen3.6-35B-A3B, Qwen3.8-27B); one resident at a time. Recognition is never sent to the cloud.
4. **Deterministic before probabilistic.** Checkers (sympy equivalence, debits = credits, sudoku validity, graph rules) run before any LLM. The LLM explains; it does not arbitrate what a checker can decide.
5. **Hint ladder.** Nudge → hint → corrected step → full solution. Never give the full solution unprompted.
6. **Confidence gate.** Low-confidence readings produce a question chip ("I read this as ∂ — correct?"), not a grade.
7. **Web only, plus one local macOS helper.** The canvas stays a web app: no native iOS app, no App Store, nothing that needs an Apple developer account. The screen tutor needs one small native macOS helper (`apps/overlay`, Swift/AppKit via SwiftPM, ad-hoc signed, run locally). The iPad uses Safari; it reaches the Mac through the Cloudflare tunnel by default (§8, DECISIONS #26). No service worker; a PWA manifest is enough.
8. **Memory budget 32 GB.** Never two large models resident. ~20 GB tier-2 model + ~3 GB tier-1 models + Chandra loaded on demand for photos + ~2 GB voice.
9. **Fixtures are the spec.** `fixtures/recordings/*.json` are real sessions with ground truth and expected tutor behaviour. Tests replay them (`RECORDINGS.md`).
10. **The screen tutor points, it never acts.** No clicking, typing or scrolling for the user; automation stays behind `ALLOW_AUTOMATION=false`. Capture pauses on secure fields and on blocklisted apps (`SCREEN_BLOCKLIST`).

## 1. System overview

```
┌──────────────────────── iPad Safari / Mac browser ────────────────────────┐
│  apps/web (React + tldraw)                                                │
│  ink layer (user) · tutor layer (marks, ghost ink) · popovers · mic/speaker│
└──────────────▲────────────── WebSocket (events) ─────────▲────────────────┘
               │ ink.update / hint / voice.audio          │ reading / feedback / speak
┌──────────────┴──────────────── Mac: apps/server ─────────┴────────────────┐
│ api ── agent (session state machine) ── modes/<subject> plugins           │
│  │        │            │            │             │                        │
│ ink    recognize    checkers      tutor         voice                      │
│ store  (tier 1,     (sympy,       (tier 2:      (VAD → STT → tutor → TTS)  │
│ raster  always       ledger,       Local MLX |                              │
│ lines   local)       sudoku,       Anthropic |                              │
│                      graph)        OpenAI)                                 │
│ runtime: launches/supervises mlx-vlm; loads/unloads Chandra; memory watch │
└────────────────────────────────────────────────────────────────────────────┘
```

One Mac serves one or a few users on the LAN. The iPad never runs models.

## 2. Core data model (`packages/protocol`)

All types are pydantic models with JSON schema export; TS types are generated from them. Field names below are normative.

```python
class Point(NamedTuple): t_ms: float; x: float; y: float; pressure: float; tilt_x: float; tilt_y: float
class Stroke(BaseModel): id: str; page_id: str; color: str; width: float; pointer_type: Literal["pen","mouse","touch"]; pts: list[Point]

class ReadingLine(BaseModel):
    id: str; stroke_ids: list[str]; bbox: BBox
    kind: Literal["math","text","label"]
    latex: str | None; text: str | None
    confidence: float                       # 0..1
    alternatives: list[str] = []            # for the confidence chip
    symbol_map: list[SymbolSpan] = []       # stroke ids → symbol index, when the recognizer knows it

class GraphNode(BaseModel): id: str; label: str; bbox: BBox; stroke_ids: list[str]; shape: str | None
class GraphEdge(BaseModel): id: str; src: str; dst: str; label: str | None; directed: bool; stroke_ids: list[str]
class Reading(BaseModel): page_id: str; lines: list[ReadingLine]; graph: Graph | None; version: int

class Anchor(BaseModel): line_id: str | None; stroke_ids: list[str] | None; bbox: BBox | None; node_id: str | None

class FeedbackEvent(BaseModel):
    id: str; page_id: str; anchor: Anchor
    type: Literal["mark","ghost","hint","question","speak","explain","summary","retract"]
    glyph: Literal["check","cross","question","pin","underline","circle"] | None
    severity: Literal["ok","minor","major"] | None
    text: str | None                        # mark label / ghost ink text / hint text / speech
    hint_level: int | None                  # 1 nudge · 2 hint · 3 corrected step · 4 solution
    refers_to: str | None                   # feedback id this supersedes (retract/replace)

class Session(BaseModel):
    id: str; mode: str; problem: str; rubric: str | None; reference_answer: str | None
    provider: Literal["local","anthropic","openai"]; voice: bool; pages: list[PageRef]
```

Events on the wire (WebSocket, JSON, one object per message):

- client → server: `ink.update {page_id, strokes_changed:[Stroke], strokes_removed:[id]}`, `hint.request {feedback_id|line_id, level}`, `question.answer {reading_line_id, chosen}`, `ghost.accept|dismiss {feedback_id}`, `voice.audio {pcm16 chunk}`, `voice.stop`, `session.update {provider, voice}`.
- server → client: `reading {Reading}` (tier 1), `feedback {FeedbackEvent}` (streamed), `status {page_id, phase}`, `voice.transcript {partial|final}`, `voice.audio {opus/pcm chunk}`.

Photos go over REST (`POST /sessions/{id}/photos`) and come back as a page whose anchors are bboxes.

## 3. Client (`apps/web`)

- **Canvas:** tldraw SDK (hobby license key or dev mode; see DECISIONS for the Excalidraw fallback). Two layers: *ink* (user-editable draw shapes) and *tutor* (locked; user may erase/accept/dismiss). Custom shapes: `MarkShape`, `GhostInkShape` (handwriting font, dashed, tutor colour), `ConfidenceChip`.
- **Input:** pointer events with `getCoalescedEvents`, pressure and tilt; `touch-action: none`; fingers ignored by default (palm rejection), toggle to allow.
- **Dirty-region tracker:** strokes added/removed since the last `reading`. On pen-up + 700 ms debounce → `ink.update`. The server rasterizes from strokes; the client never sends images for ink.
- **Rendering feedback:** `mark` → glyph at anchor (margin for line anchors, overlay for stroke anchors); `ghost` → text in the tutor's hand beneath/beside the anchor; `hint`/`explain` → popover content for the anchor; `question` → chip; `speak` → audio player state; `retract` → remove by `refers_to`.
- **Hint ladder UI:** tapping a mark opens the popover at level 1; "more" requests the next level.
- **Settings:** provider toggle with badge (`local · 3.4 s` / `Claude · 1.9 s`), voice on/off, "show what you read" debug pane (renders `Reading`), finger-draws.
- **PWA:** manifest only; no service worker. Pair via QR shown by the server.

## 4. Server (`apps/server`)

### 4.1 Modules
- `api/` FastAPI: REST (sessions, photos, settings, pairing QR) + `ws/session/{id}`.
- `ink/` stroke store per page; rasterizer (skia-python, 2× scale, white paper, no grid); line grouping (y-overlap clustering with x-gap heuristics); diagram pre-pass (closed loops → candidate boxes, polylines with arrowheads → candidate edges).
- `recognize/` tier 1 (always local):
  - `MathRecognizer` — mathnote-ocr (stroke-based, incremental, keeps stroke→symbol map, supports pinning a confirmed subtree).
  - `TextRecognizer` — Qwen3.5-4B (MLX 4-bit) with bbox grounding, for prose and CJK.
  - `DiagramRecognizer` — geometric pre-pass + VLM for labels/arrows → `Graph` with bboxes.
  - `PhotoRecognizer` — Chandra OCR 2 (MLX mixed 4/8) for paper photos; loaded on demand, unloaded after.
  - All return a `Reading`; each line carries `confidence` and `alternatives`.
- `checkers/` deterministic: `math.equivalent(prev, curr)` via sympy (parse LaTeX → expression; handle equations, inequalities, common student forms); `ledger.balanced(entries)`; `sudoku.valid(grid)`; `graph.rules(graph, rubric)` (orphans, SPOF, missing edge types). Return `CheckResult {ok, kind, anchor, detail}`.
- `tutor/` tier 2. One interface, three providers:
  ```python
  class Tutor(Protocol):
      async def review(self, ctx: ReviewContext) -> AsyncIterator[FeedbackEvent]: ...
      async def hint(self, ctx: HintContext) -> AsyncIterator[FeedbackEvent]: ...
      async def converse(self, ctx: VoiceContext) -> AsyncIterator[FeedbackEvent]: ...
  ```
  `LocalMLX` → OpenAI-compatible call to mlx-vlm/LM Studio; `Anthropic` → Messages API with images; `OpenAI` → Responses API. Same prompt templates (`tutor/prompts/<mode>.md`), JSON-lines structured output, pydantic-validated, streamed, cancellable. Context = image crops of changed regions + `Reading` + `CheckResult`s + problem/rubric + prior feedback (so it does not re-mark) + the user's current hint level.
- `agent/` session agent (see §5).
- `voice/` VAD (Silero) → streaming STT (Parakeet TDT via mlx-audio; Whisper large-v3-turbo fallback for other languages) → `Tutor.converse` → TTS (Kokoro-82M or Qwen3-TTS via mlx-audio). Barge-in: incoming speech cancels TTS playback and the current `speak` events. When provider ≠ local, swap STT/TTS/LLM for the provider's realtime speech API, keep local as fallback.
- `runtime/` starts and supervises the mlx-vlm server for the tier-2 model; loads/unloads Chandra; memory watchdog (`vm_stat`); refuses to load a second large model.
- `store/` SQLite (sessions, pages, strokes, readings, feedback) + files (rasters, photos). Recordings in `fixtures/` use the same schema as exports.

### 4.2 Modes as plugins (`modes/<name>/`)
```
modes/math/        config.toml (recognizer=math, checkers=[math.equivalent], marks=[check,cross,underline])
                   rubric.md   (what good work looks like; hint-ladder guidance)
modes/system_design/ config.toml (recognizer=diagram, checkers=[graph.rules], marks=[pin,circle])
                   rubric.md   (components, flows, failure modes, scale questions)
modes/consulting/  … issue trees, MECE, market sizing arithmetic
modes/language/    … recognizer=text, checkers=[], rubric per target language
modes/accounting/  … recognizer=text+table, checkers=[ledger.balanced]
modes/sudoku/      … recognizer=grid, checkers=[sudoku.valid], tutor explains next deducible move only
```
Adding a subject = adding a folder. The agent loads `config.toml` and `rubric.md`; the prompt template is shared.


### 4.3 Choosing the local tier-2 model (config var + UI toggle)

The local provider is parameterised by a model key, not hard-wired:

- `LOCAL_MODEL=<key>` in `.env` selects an entry under `[tier2.*]` in `models/manifest.toml` (`qwen36-35b-a3b` default, `gemma4-26b-a4b`, `qwen38-27b`, …). Adding a model = adding a manifest entry.
- `GET /settings` exposes the available keys with memory and loaded state; the web settings panel shows a picker under the provider toggle. Changing it calls `PUT /settings {local_model}`.
- `runtime/` owns the swap: it stops the mlx-vlm server, frees memory, verifies the budget (`memory_gb` of the new model + resident tier-1/voice ≤ budget), starts mlx-vlm with the new model, waits for `/v1/models` to answer, then flips `LocalMLX` to it. A swap takes ~10–40 s; the UI shows "loading <model>" and keeps the previous model's answers visible. Only one tier-2 model is ever resident.
- The provider badge includes the model key (`local · gemma4-26b-a4b · 2.8 s`), and every `FeedbackEvent` batch is logged with provider + model so fixture judge results can be compared per model (`make judge MODEL=gemma4-26b-a4b`).
- Prompts are shared; per-model quirks (e.g. bbox output format) live in `tutor/adapters/<key>.py`, kept minimal.

## 5. The agent (`apps/server/agent`)

Not a chat bot: a constrained review loop per page.

States: `idle → recognizing → checking → reviewing → waiting`. Transitions:
- `ink.update` → cancel any in-flight review → `recognizing` (tier 1 on dirty region; merge into `Reading`, bump version).
- Low-confidence lines → emit `question` chips; those lines are excluded from checking/reviewing until answered.
- `checking` → run mode checkers on new/changed lines → `CheckResult`s. Emit `mark` events directly for checker verdicts (✓ for equivalent step, ✗ + underline for a failed one). These are fast and do not wait for the LLM.
- `reviewing` → one tier-2 `review` call with the delta (changed lines + context). The LLM may add marks, a short `speak`, and prepared hint text (stored, not shown until requested). It must not contradict a checker; if it disagrees it emits `question`, not `mark`.
- `hint.request` → `hint` call at the requested level, cached per line.
- `waiting` → idle until new ink, a tap, or speech.

Rules enforced in code, not just prompt: at most one tier-2 call in flight per page; new ink cancels; `FeedbackEvent`s referencing unknown anchors are dropped; `hint_level 4` only when the user asked for it; `speak` text ≤ 20 words unless the user asked a question.

Pedagogy rules in the prompt: mark the minimal strokes; prefer a question to a wrong mark; never write on the page except as `ghost`; explanations go to `explain` (popover); keep the student's notation.

Tools exposed to the LLM: the checkers and `ask_user`. Nothing else.

## 6. Voice channel

Audio and ink feed the same session agent; voice never bypasses the marks layer.

- Input: browser mic → PCM16 16 kHz chunks over the WebSocket → Silero VAD → Parakeet TDT streaming → `voice.transcript` partials to the client → on end-of-utterance, `Tutor.converse` with the transcript + the pen's current anchor (what the user is pointing at) + page context.
- Output: `speak` events → TTS → `voice.audio` chunks → client plays; mirrored as text in the panel. Unprompted speech is short ("careful with the sign there"); detail stays in `explain`.
- Speech controls the hint ladder: "just a hint", "show me the step".
- Barge-in: user speech stops playback and cancels pending `speak`.
- Memory: VAD + Parakeet + Kokoro ≈ 2 GB.

## 7. Photos

`POST /sessions/{id}/photos` → dewarp/crop (OpenCV, optional) → Chandra → `Reading` with bbox anchors → same checking/reviewing. The client shows the photo as the page background; marks anchor to bboxes.

## 8. Networking and pairing

Server binds `0.0.0.0:8000`. **The iPad's usual way in is the HTTPS Cloudflare quick tunnel** (DECISIONS #26: it works on networks you don't control, such as venue/guest Wi-Fi, and Safari only allows the microphone on HTTPS): `make ipad` starts the server, opens the token-gated tunnel and prints its QR (`make pair` does the tunnel alone, next to a running server). Where the LAN allows it, the start screen's "Pair iPad" dialog also shows `http://<lan-ip>:8000` and `http://<host>.local:8000` (Pencil input works over plain HTTP; the mic doesn't). While pairing, the server advertises `_http._tcp` "Ink Tutor" via Bonjour — iOS Safari doesn't browse Bonjour, so the QR is the way in.

## 9. Non-functional targets

| Metric | Target |
|---|---|
| Tier-1 reading after debounce | < 300 ms |
| First tutor mark (local) | < 4 s |
| Hint response (cached context) | < 2 s |
| Voice: speech end → first audio | < 1.5 s local |
| Memory | < 28 GB resident; never two large models |
| Privacy | local by default; cloud toggle shows exactly what leaves (crops + transcript) |

## 10. Testing strategy

- Unit: checkers (sympy forms), line grouping, rasterizer determinism.
- Fixture replay (`fixtures/recordings/*.json`): feed strokes with original timing (or ×N), assert tier-1 `truth` match per checkpoint (CER / LaTeX normalisation), assert checker verdicts, and run an LLM-judge comparing tier-2 output to `expect` (local judge by default; the judge prompt lives in `apps/server/tests/judge.md`).
- Contract: protocol JSON schema snapshot test; TS types regenerated and diffed.
- E2E: Playwright draws a recorded session into the canvas via synthetic pointer events and checks marks appear at the right anchors.

## 11. Screen tutor mode

Full design: `SCREEN-TUTOR.md`. Mac only: the "page" is the user's screen, read as screenshots plus the accessibility tree, and the tutor answers with highlights, arrows and captions drawn by a small native helper (`apps/overlay`, Swift/AppKit) over any app. It shares the tier-2 model (Gemma 4 by default, DECISIONS #25), the agent skeleton (observe → check → guide → wait), the hint ladder, voice (local, #27) and the provider toggle (`Session.provider`, `_ask_tutor`'s local fallback). The differences are two adapters:

- **Input:** `screen.frame` (downscaled JPEG, base64) and `ax.snapshot` from the helper, plus `input.event` (click/key, no content) as a cue to re-snapshot. Server modules `screen/` (ingestion, frame diff, crops), `ax/` (element search, predicates) and `guide/` (planner, stepper, replanner).
- **Output:** `overlay {OverlayEvent}` (highlight, arrow, caption, pulse, clear, phantom_state), `guide.plan` and `guide.status` instead of `feedback`/`status`.

Protocol additions go next to the ink types in `inktutor_protocol/models.py`, same style (`_M`, `BBox`, dotted `type` literals): `AXElement`, `AXSnapshot`, `ScreenFrame`, `InputEvent`, `GuideGoal`, `GuideControl`, `Target`, `DoneWhen`, `GuideStep`, `GuidePlan`, `OverlayEvent` and the wrappers `OverlayMsg`, `GuidePlanMsg`, `GuideStatusMsg`, `ScreenConfigMsg`, in their own `ScreenClientMessage`/`ScreenServerMessage` unions; `Mode` gains `"screen"`. `Tutor` gains `plan` and `locate`. The helper connects to `ws://127.0.0.1:8000/ws/screen/{id}`, localhost only: screen data never goes through the Cloudflare tunnel (§8).
