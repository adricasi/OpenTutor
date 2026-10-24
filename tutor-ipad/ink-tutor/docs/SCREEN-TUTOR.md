# Screen tutor (software walkthroughs on the Mac)

Status: design (planned). Second mode of the same product: instead of a canvas, the "page" is the user's Mac screen; instead of strokes, the input is screenshots plus the accessibility tree; instead of marks on ink, the output is highlights, arrows and captions drawn over any application. Same brain, same voice, same rules.

The design is written against the app as built in `tutor-app/` (DECISIONS #13): same protocol style, providers, voice stack and mode folders. DECISIONS #31 summarises the choices.

## The experience

1. The user asks, by voice (default) or text: "how do I make a pivot table of sales by region?"
2. A floating, draggable **phantom icon** sits on the screen. Clicking it opens a small menu: text input, listen on/off, show transcript, pause, exit.
3. The tutor plans the steps, then guides **one step at a time**: a highlight box around the control on the user's actual screen, an arrow if the target is far from the mouse, a short caption, and a short spoken line ("Click *Insert* in the ribbon").
4. **The user does the clicking.** The system watches the screen, detects that the step is done, and advances automatically. If the user does something else, it adapts (replans) rather than insisting.
5. Hint ladder applies: first the location, then why, then what the control does, then the full sequence — never doing it for the user (automation is a later flag, off by default).

Over any app: Excel, Numbers, Figma, Terminal, browser, system settings.

## Components

```
tutor-app/apps/overlay/  (macOS helper, Swift + AppKit, built with SwiftPM, ad-hoc signed, runs locally)
  ├─ Overlay window: transparent, click-through, above all apps and all displays; draws highlight/arrow/caption
  ├─ Phantom icon: draggable floating button + menu (text, listen on/off, transcript, pause, exit)
  ├─ Capture: ScreenCaptureKit frames at ~1 fps, plus a frame on every click and on focused-window change; downscaled
  ├─ AX reader: Accessibility API snapshot of the frontmost app (windows, elements: role, title, value, frame, enabled, path)
  ├─ Input hooks: global click/keystroke *events only* (no content), to know when the user acted
  ├─ Voice I/O: mic → PCM16 16 kHz `voice.audio`; plays `voice.audio` back  (the browser does this for the
  │            canvas; VAD/STT/TTS stay in the server's voice/ stack)
  └─ Client of apps/server: ws://127.0.0.1:8000/ws/screen/{session_id}, messages from packages/protocol
               (own endpoint, localhost only; see "Networking")

tutor-app/apps/server/src/inktutor_server/
  ├─ screen/   frame ingestion, change detection (perceptual hash diff), region crops around targets
  ├─ ax/       AX snapshot model, element search (by role+title+path), predicates ("dialog titled X exists")
  ├─ guide/    planner (goal → ordered steps with target descriptors and done_when predicates), stepper state
  │            machine, replanner; GuideAgent = the screen variant of agent/
  └─ tutor/, voice/, runtime/, agent/  shared with the ink tutor

tutor-app/modes/screen/{config.toml,rubric.md}   the mode's knobs + rubric, like every subject
tutor-app/modes/apps/<app>.md                    optional recipes: known AX paths and step templates for common
                                                 tasks (Excel pivot table, …); not a subject (no config.toml)
tutor-app/apps/server/src/inktutor_server/tutor/prompts/screen.md   ## rules · ## plan · ## locate · ## converse
```

Permissions needed once (macOS prompts): Screen Recording, Accessibility, Microphone. No Apple developer account: the helper is built and run locally (ad-hoc signed). See "Setup notes".

## Data model additions (`packages/protocol`)

*(Written in the style of `inktutor_protocol/models.py`: `_M` base (`extra="forbid"`), `BBox` reused, client messages carry a dotted `type` literal, server messages wrap their payload like `FeedbackMsg`; images travel as base64 strings because the wire is JSON over WebSocket, like `VoiceAudio.pcm16_b64` and the photo upload's `data_url`.)*

All screen-mode `BBox`es and points are **global display points** in AX coordinates (origin top-left of the main display, y down); the helper converts to AppKit coordinates for drawing. *(One coordinate space, so AX frames and VLM boxes line up.)*

```python
Mode = Literal[..., "screen"]                                  # Session.mode; Session.pages stays empty
GuidePhase = Literal["idle", "planning", "locating", "guiding", "verifying", "replanning", "paused", "done"]

class Point2(_M): x: float; y: float

class AXElement(_M):
    id: str; role: str; title: str | None = None; value: str | None = None   # value never sent for secure fields
    frame: BBox; enabled: bool = True
    path: list[str] = []                                       # role/title chain from the window

# ---- wire: overlay → server
class AXSnapshot(_M):
    type: Literal["ax.snapshot"] = "ax.snapshot"
    app: str; window_title: str = ""; display: int = 0
    elements: list[AXElement] = []                             # visible elements only; empty while a secure field is focused
    focused_id: str | None = None; secure_field_focused: bool = False
    pointer: Point2 | None = None                              # cursor at snapshot time (for the 300 px arrow rule)
    t_ms: float

class ScreenFrame(_M):
    type: Literal["screen.frame"] = "screen.frame"
    id: str; display: int
    origin: Point2                                             # display's top-left in global points (maps VLM boxes)
    width: int; height: int; scale: float                      # JPEG px; JPEG px per point
    jpeg_b64: str                                              # base64: the wire is JSON
    window: BBox | None = None                                 # frontmost window: the only crop the cloud ever gets
    t_ms: float; trigger: Literal["tick", "click", "focus"]

class InputEvent(_M):
    type: Literal["input.event"] = "input.event"
    kind: Literal["click", "key"]; t_ms: float
    at: Point2 | None = None                                   # click position; keys carry no content, not even key codes

class GuideGoal(_M):    type: Literal["guide.goal"] = "guide.goal"; text: str          # phantom text box
class GuideControl(_M): type: Literal["guide.control"] = "guide.control"
                        action: Literal["next", "back", "repeat", "pause", "resume", "stop"]

ScreenClientMessage = Annotated[ScreenFrame | AXSnapshot | InputEvent | GuideGoal | GuideControl
                                | SessionUpdate | VoiceAudio | VoiceStop, Field(discriminator="type")]

# ---- plan (server-internal + sent to the overlay's transcript panel)
class Target(_M):
    ax_path: list[str] | None = None; title_regex: str | None = None
    bbox: BBox | None = None
    source: Literal["ax", "vlm"] | None = None                 # None until located (the planner doesn't know yet)
class DoneWhen(_M):
    kind: Literal["ax_exists", "ax_value", "window_title", "vlm_yes_no", "user_next"]; args: dict[str, object] = {}
class GuideStep(_M):
    id: str; n: int; instruction: str; speak: str              # speak ≤ 15 words (code-enforced)
    target: Target | None = None; done_when: DoneWhen
    hint_levels: list[str] = []                                # rungs 1–3; rung 4 (full sequence) is the plan itself
class GuidePlan(_M): goal: str; app: str; steps: list[GuideStep]; version: int = 0

class OverlayEvent(_M):
    id: str                                                    # like FeedbackEvent.id, so `clear` can refer to it
    type: Literal["highlight", "arrow", "caption", "pulse", "clear", "phantom_state"]
    bbox: BBox | None = None; from_point: Point2 | None = None; text: str | None = None
    step_id: str | None = None; ttl_ms: int | None = None
    state: Literal["idle", "listening", "thinking", "speaking", "paused", "capture_paused"] | None = None  # phantom_state

# ---- wire: server → overlay
class OverlayMsg(_M):      type: Literal["overlay"] = "overlay"; event: OverlayEvent; provider: str = ""; model: str = ""
class GuidePlanMsg(_M):    type: Literal["guide.plan"] = "guide.plan"; plan: GuidePlan
class GuideStatusMsg(_M):  type: Literal["guide.status"] = "guide.status"; phase: GuidePhase; step: int = 0; of: int = 0; detail: str = ""
class ScreenConfigMsg(_M): type: Literal["screen.config"] = "screen.config"; blocklist: list[str]; capture_fps: float = 1.0
                           allow_automation: bool = False      # the helper enforces the blocklist before capture

ScreenServerMessage = Annotated[OverlayMsg | GuidePlanMsg | GuideStatusMsg | ScreenConfigMsg | SessionMsg
                                | VoiceTranscript | VoiceAudioOut | ErrorMsg | RuntimeMsg, Field(discriminator="type")]
```

Wire events (reusing the existing names): overlay → server: `screen.frame`, `ax.snapshot`, `input.event`, `guide.goal` (phantom text), `guide.control` (phantom pause/exit, voice next/back/repeat/stop), `session.update {voice, provider}` (phantom listen on/off, provider toggle), `voice.audio`, `voice.stop`. Server → overlay: `overlay {OverlayEvent}`, `guide.plan`, `guide.status {phase, step n/N}`, `screen.config` (on connect), and the existing `session`, `voice.transcript`, `voice.audio`, `runtime`, `error`. There is no separate `speak` message: a step's `speak` line goes out as a `caption` (the transcript panel's text) plus `voice.audio` whose `feedback_id` is that caption's id.

The helper's `Codable` structs mirror `schema.json` (`make protocol` exports both unions); a server test validates every message in `fixtures/screens/*.json` against the pydantic models, so the Swift side can't drift silently.

## The guide loop (agent state machine, screen variant)

`idle → planning → locating → guiding → verifying → (next step | replanning) → done`, plus `paused` (phantom pause, secure field, blocklisted app). Each transition is a `guide.status`. *(The canvas agent's states — `idle → recognizing → checking → reviewing → waiting` — stay as they are; `guide/` reuses the same building blocks, listed below.)*

- **planning:** tier-2 with the goal, app name, window title, a downscaled screenshot and a compact AX summary → `GuidePlan` (JSON, validated). Recipes in `modes/apps/<app>.md` are prepended when the app matches; they make plans precise and cheap. *(The plan streams as JSON lines, one `{"type":"step",…}` per line, validated as it arrives like `FeedbackEvent`s, so step 1 can be located while the rest streams.)*
- **locating:** resolve `Target` with AX first (exact frame, exact name). Only if AX has no match (canvas-style apps, web content without roles) fall back to the VLM: send the screenshot, ask for the bbox of the described control (the step's `instruction`), snap to the nearest AX frame if one overlaps; VLM boxes come in JPEG pixels and are mapped to global points with the frame's `origin` and `scale`.
- **guiding:** emit `highlight` + `caption` + the spoken line; `arrow` from the pointer if the target is > 300 px away; `pulse` after 8 s of inactivity.
- **verifying:** evaluate `done_when` on every AX snapshot / frame (debounced 300 ms). `ax_exists` ("a sheet named PivotTable…" / "dialog titled Create PivotTable"), `ax_value` (a field now contains X), `window_title`; `vlm_yes_no` only as a fallback and only on frames that changed. On success: `clear`, advance. If the user's action led somewhere unexpected (focused app changed, a different dialog opened): `replanning` from the current state, keeping the goal; say so in one short line.
- **safety:** never emit a step that types into or reads a secure text field (AX `secure_field_focused` pauses capture and says "I'll wait"). Never click, type or scroll (automation is a later feature behind `ALLOW_AUTOMATION=false`; no overlay or wire message can act on the user's screen).

Rules in code, not prompt: one tier-2 call in flight per guide, and a newer screen state (focus change, unexpected dialog) cancels a `locate`/`verify` still in flight for the old one (the "new ink cancels" rule); `vlm_yes_no` no more than once per 2 s; plan length ≤ 15 steps (else split into sub-goals); every `highlight` must carry a bbox inside the display bounds; events naming a `step_id` outside the current plan are dropped (like unknown anchors); spoken lines ≤ 15 words unless the user asked a question. Knobs (300 px, 8 s, 300 ms, 2 s, 15 steps, capture fps) live in `modes/screen/config.toml`.

**What it reuses:**
- **`Tutor` interface** (`tutor/base.py`): two new methods next to `review`/`hint`/`converse`, implemented by the same three providers (no new client):
  ```python
  def plan(self, ctx: PlanContext) -> AsyncIterator[dict[str, Any]]: ...      # JSON lines: {"type":"step", …}
  def locate(self, ctx: LocateContext) -> AsyncIterator[dict[str, Any]]: ...  # {"type":"bbox", …} or, for vlm_yes_no, {"type":"yes_no","yes":…}
  ```
  `PlanContext` / `LocateContext` are dataclasses like `ReviewContext` (JPEG bytes decoded from `jpeg_b64` at ingestion; AX summary as text; recipe; steps already done for a replan). Prompts in `tutor/prompts/screen.md`, sections loaded by `load_prompt(mode, kind)`.
- **Provider selection and fallback:** `Session.provider` (set by `session.update`), `Agent._ask_tutor`'s cloud → local fallback chain, `answered_by` → `OverlayMsg.provider/model` for the badge in the phantom menu. `_ask_tutor` gets lifted into a shared helper so `guide/` can call it (today it takes a `PageState` only to emit the error `StatusMsg`).
- **Hint ladder** (non-negotiable 5, DECISIONS #19): rung 1 = the location (the highlight itself), 2 = why, 3 = what the control does, 4 = the full remaining sequence, only on an explicit request ("show me all the steps", the "solution" voice command). Rungs come from `GuideStep.hint_levels`; "hint"/"more" climb with `_voice_commands.next_level`, as on the canvas.
- **Voice:** the same `voice/` session (Silero → Parakeet → Kokoro). Questions ("why?", "what does this do?") go to `Tutor.converse` with the current step as context (`VoiceContext` gains `step: str | None`; `pen_line` is None in screen mode).
- **`RuntimeStatus`:** the overlay gets the same `runtime` message every 5 s; the phantom menu shows the memory light and each `event` once (e.g. "tutor switched to slower mode").
- **Model:** Gemma 4 26B-A4B, the default local tutor (DECISIONS #25), does planning and the VLM fallback; no extra model is loaded, so the memory budget is unchanged. Its bbox accuracy on screenshots is unmeasured; the Perception/Guide phases measure it on `fixtures/screens/`.

## Completion detection details

- Prefer *state* predicates ("PivotTable Fields pane exists") over *action* predicates ("user clicked Insert"): they survive the user taking a different route.
- Use input events only as hints to re-snapshot immediately after a click, not as proof of completion.
- Screenshot diff gates the VLM fallback: no change → no call.

## Voice and the phantom icon

- Voice is the default channel (same `voice/` stack). The phantom icon shows listening state; its menu offers text input for quiet places, listen on/off, a transcript panel, pause, exit.
- Spoken lines ≤ 15 words per step; details on request ("why?", "what does this do?").
- Barge-in and "next"/"back"/"repeat" voice commands; "stop" exits the walkthrough. *(These go into `voice/session.py`'s `COMMANDS` next to "hint"/"more"/"step"/"solution", EN/ES/CA, and become `guide.control` actions. Rules: only in screen sessions; placed after "more" so "next hint" stays a hint; matched only on short whole utterances (≤ 4 words), anything longer goes to `converse`; Spanish "para" is not "stop" (it also means "for") — use "stop", "parar", "detente", "prou".)*
- Voice stays local (DECISIONS #27): with the cloud toggle on, only the text of `plan`/`locate`/`converse` calls goes to the provider, never audio. *(ARCHITECTURE §4.1's "provider's realtime speech API" does not apply here.)*

## Privacy

Screenshots and AX data stay on the Mac. With the cloud toggle on, the user is shown that a downscaled screenshot of the frontmost window (not other displays, not other windows) is what leaves the machine; capture pauses on secure fields and on apps listed in `SCREEN_BLOCKLIST` *(an env setting like every other knob, sent to the helper in `screen.config` so blocklisted windows are never captured at all, and the server drops anything that slips through)* (password managers, banking). A visible indicator shows when capture is active. Frames are held in memory only (the last few per display) and never logged; only the screen recorder writes them to disk.

**Networking** (DECISIONS #26): the iPad reaches the Mac through the Cloudflare tunnel, but the screen tutor is Mac-only: the helper connects over localhost, and the server accepts `/ws/screen/*` only from localhost (requests carrying the tunnel's `cf-connecting-ip`/`cf-ray` headers are refused, `pairing.is_tunnel_request`). Screenshots and AX data never pass Cloudflare's edge.

## Fixtures for this mode

`ink-tutor/tools/screen-recorder/` (to build): records a manual walkthrough as `AXSnapshot`s + downscaled frames + input events + spoken narration, into `ink-tutor/fixtures/screens/*.json`. Tests replay them: the planner must produce a plan whose targets resolve by AX on the recorded snapshots; the stepper must advance at the recorded completion moments. First fixture: Excel pivot table (Sales by Region), then Numbers chart, then a Finder/System Settings task to prove it is app-agnostic.

## Setup notes (checked on the reference Mac, M3 Pro, 36 GB, 2026-10-24)

- macOS 14.3 (23D56), arm64: ScreenCaptureKit (12.3+) is available, and so is `SCScreenshotManager` (14.0+) for single on-click frames.
- Swift toolchain present: Apple Swift 5.10 (swiftlang-5.10.0.13), SwiftPM 5.10, from the **Command Line Tools 15.3** (`xcode-select -p` → `/Library/Developer/CommandLineTools`); no Xcode.app. SDK 14.4 includes ScreenCaptureKit, AppKit, ApplicationServices (AX) and AVFAudio; `codesign` is there, so `codesign -s -` (ad-hoc) needs no account.
- **No XCTest** in the Command Line Tools, so `swift test` won't run without Xcode.app: the helper's logic gets tested through the server (fixture replays, contract test), plus a small executable self-check if needed.
- PyObjC is not in `tutor-app/uv.lock` (the Python alternative would add it).
- `make overlay` wraps the SwiftPM executable in a small `.app` (Info.plist with `NSMicrophoneUsageDescription`, `LSUIElement` so it has no Dock icon), signs it ad hoc and launches it.
- Expect: macOS ties Screen Recording/Accessibility grants to the code signature, and an ad-hoc signature changes on every rebuild, so permissions may be asked again after rebuilds. A stable self-signed certificate made in Keychain Access (no developer account) avoids that.

## Why this shares the architecture

Same tier-2 model (bounding boxes, screen understanding), same agent skeleton (observe → check → guide → wait), same hint ladder and never-do-it-for-them rule, same voice, same provider toggle, same memory budget. The differences are two adapters: an input adapter (screen + AX instead of strokes) and an output adapter (overlay instead of marks on ink).
