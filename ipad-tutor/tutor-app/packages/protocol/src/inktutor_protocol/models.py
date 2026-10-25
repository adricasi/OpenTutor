"""The one contract between server and web (ARCHITECTURE §2). Field names are normative.

Change a type here → `make protocol` regenerates `schema.json` and the TS types → then touch server and web.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

# (t_ms, x, y, pressure, tilt_x, tilt_y) — CSS px of the page, t relative to session start.
Point = tuple[float, float, float, float, float, float]
PointerType = Literal["pen", "mouse", "touch"]
Mode = Literal["math", "diagram", "system_design", "consulting", "language", "accounting", "sudoku", "screen", "other"]
Provider = Literal["local", "anthropic", "openai"]


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BBox(_M):
    x: float
    y: float
    w: float
    h: float


class Stroke(_M):
    id: str
    page_id: str
    color: str = "#1F2328"
    width: float = 3
    pointer_type: PointerType = "pen"
    pts: list[Point]


class SymbolSpan(_M):
    symbol: str
    index: int  # position in the line's latex/text
    stroke_ids: list[str]


class ReadingLine(_M):
    id: str
    stroke_ids: list[str]
    bbox: BBox
    kind: Literal["math", "text", "label", "long_division"]
    latex: str | None = None
    text: str | None = None
    confidence: float = Field(ge=0, le=1)
    alternatives: list[str] = []
    symbol_map: list[SymbolSpan] = []
    structure: dict[str, object] | None = None  # e.g. long division {dividend, divisor, quotient, rows}
    corrected: Literal["typed", "spoken"] | None = None  # the student told the tutor what this line says


class GraphNode(_M):
    id: str
    label: str
    bbox: BBox
    stroke_ids: list[str]
    shape: str | None = None


class GraphEdge(_M):
    id: str
    src: str
    dst: str
    label: str | None = None
    directed: bool = True
    stroke_ids: list[str] = []


class Graph(_M):
    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []


class Reading(_M):
    page_id: str
    lines: list[ReadingLine] = []
    graph: Graph | None = None
    version: int = 0


class Anchor(_M):
    line_id: str | None = None
    stroke_ids: list[str] | None = None
    bbox: BBox | None = None
    node_id: str | None = None


# "formula": a short LaTeX note the tutor writes beside a line while answering by voice (display only: never read
# back as the student's work, never graded; retracted with the line like everything else)
FeedbackType = Literal["mark", "ghost", "hint", "question", "speak", "explain", "summary", "retract", "formula"]
Glyph = Literal["check", "cross", "question", "pin", "underline", "circle"]


class FeedbackEvent(_M):
    id: str
    page_id: str
    anchor: Anchor
    type: FeedbackType
    glyph: Glyph | None = None
    severity: Literal["ok", "minor", "major"] | None = None
    text: str | None = None  # mark label / ghost ink text / hint text / speech
    hint_level: int | None = Field(default=None, ge=1, le=4)  # 1 nudge · 2 hint · 3 corrected step · 4 solution
    refers_to: str | None = None  # feedback id this supersedes (retract/replace)
    source: Literal["checker", "tutor", "recognizer"] = "tutor"
    choices: list[str] = []  # for type="question": one-tap answers


class PageRef(_M):
    id: str
    index: int
    width: float
    height: float


class Session(_M):
    id: str
    mode: Mode
    problem: str = ""
    rubric: str | None = None
    reference_answer: str | None = None
    provider: Provider = "local"
    voice: bool = False
    pages: list[PageRef] = []
    keep_history: bool = False  # keep a short summary of wiped/erased lines as prior work for the tutor
    student: str | None = None  # first name typed on a homework link (teacher view)
    homework_id: str | None = None  # the homework this session answers, if any


class CheckResult(_M):
    ok: bool
    kind: str  # e.g. "math.equivalent", "arithmetic.long_division"
    anchor: Anchor
    detail: str
    expected: str | None = None  # what the checker computed (never shown unprompted)


# ---------------------------------------------------------------- teacher view: REST, not wire
class SessionCreate(_M):
    """POST /api/sessions. `student` / `homework_id` come from a homework link (`/?hw=<id>`)."""

    model_config = ConfigDict(extra="ignore")  # a REST body: unknown fields are ignored, as before
    mode: Mode = "math"
    problem: str = ""
    provider: Provider | None = None
    student: str | None = Field(default=None, max_length=40)
    homework_id: str | None = None


class HomeworkCreate(_M):
    title: str = Field(min_length=1, max_length=120)
    mode: Mode = "math"
    problem: str = Field(default="", max_length=2000)
    due: str | None = None  # ISO date (YYYY-MM-DD)
    answer: str | None = Field(default=None, max_length=200)  # final answer: done when a ✓ line reaches it


class Homework(HomeworkCreate):
    id: str
    created: float  # unix seconds


class HomeworkPublic(_M):
    """What a student's device sees for a homework link: never who else did it."""

    id: str
    title: str
    mode: Mode
    problem: str
    due: str | None = None


class VerdictCount(_M):
    kind: str  # checker kind, e.g. "math.equivalent", "ledger.balanced"
    ok: int = 0
    wrong: int = 0


class LogLine(_M):
    """One checked line in the learning log (the reading is short text; no strokes, no images)."""

    line_id: str
    page_id: str
    kind: str
    reads: str = ""
    ok: bool | None = None  # the line's latest verdict
    was_wrong: bool = False
    fixed: bool = False  # it was ✗ and the student fixed it (✓ on the same line region, or a ghost retired)
    label: str = ""  # the checker's short mark text for the mistake
    detail: str = ""  # the checker's explanation (for the summary writer)
    hint_level: int = 0  # highest hint level asked for on this line


class LearningLog(_M):
    """Derived, deterministically, from what the store already holds (progress.py)."""

    session_id: str
    mode: Mode
    problem: str = ""
    student: str | None = None
    homework_id: str | None = None
    created: float = 0  # session start (unix seconds)
    last_activity: float = 0  # last stored event (unix seconds)
    lines_written: int = 0
    lines_checked: int = 0
    verdicts: list[VerdictCount] = []
    mistakes: int = 0  # lines that were ✗ at some point
    mistakes_fixed: int = 0
    ghosts_retired: int = 0  # corrected steps the student then wrote themselves
    hints: list[int] = [0, 0, 0, 0]  # hints asked for, per level 1..4 (level 4 = full solution, explicit)
    questions_asked: int = 0  # "I read this as … — correct?" chips
    questions_answered: int = 0
    corrections: int = 0  # "tell the tutor what you meant" (typed or spoken)
    tutor_marks: int = 0  # marks the tutor placed on lines no checker decides
    strokes: int = 0
    time_on_task_s: float = 0  # first to last ink
    finished: bool | None = None  # None: this mode has no checker that can say so
    finished_by: Literal["answer", "last_line", ""] = ""
    lines: list[LogLine] = []


class ProgressSummary(_M):
    session_id: str
    text: str
    source: str  # "template", or "<provider> · <model>" of the tutor that wrote it
    log_hash: str
    created: float
    stale: bool = False  # the log changed since it was written


StudentStatus = Literal["done", "in_progress", "not_started"]


class StudentRow(_M):
    student: str
    status: StudentStatus
    session_id: str  # the session shown (finished, else the latest with ink)
    sessions: list[str] = []
    time_on_task_s: float = 0
    hints: int = 0
    hints_explicit: int = 0  # level-4 requests
    mistakes: int = 0
    mistakes_fixed: int = 0
    last_activity: float = 0
    summary: ProgressSummary | None = None


class HomeworkProgress(_M):
    homework: Homework
    link: str  # for students: the tunnel link when `make pair` runs, else the LAN one
    qr: str  # SVG data URI of `link`
    rows: list[StudentRow] = []


class SessionBrief(_M):
    session_id: str
    mode: Mode
    problem: str = ""
    student: str | None = None
    created: float = 0
    log: LearningLog
    summary: ProgressSummary | None = None


# ---------------------------------------------------------------- wire: client → server
class InkUpdate(_M):
    type: Literal["ink.update"] = "ink.update"
    page_id: str
    strokes_changed: list[Stroke] = []
    strokes_removed: list[str] = []
    page: PageRef | None = None  # sent with the first update of a page


class HintRequest(_M):
    type: Literal["hint.request"] = "hint.request"
    page_id: str
    feedback_id: str | None = None
    line_id: str | None = None
    level: int = Field(ge=1, le=4)
    explicit: bool = False  # True only when the user asked for this level themselves (required for level 4)


class QuestionAnswer(_M):
    type: Literal["question.answer"] = "question.answer"
    page_id: str
    feedback_id: str
    reading_line_id: str
    chosen: str


class GhostAction(_M):
    type: Literal["ghost.accept", "ghost.dismiss"]
    page_id: str
    feedback_id: str


class MarkDismiss(_M):
    type: Literal["mark.dismiss"] = "mark.dismiss"
    page_id: str
    feedback_id: str


class SessionUpdate(_M):
    type: Literal["session.update"] = "session.update"
    provider: Provider | None = None
    voice: bool | None = None
    problem: str | None = None
    mode: Mode | None = None
    keep_history: bool | None = None


class VoiceAudio(_M):
    type: Literal["voice.audio"] = "voice.audio"
    pcm16_b64: str
    sample_rate: int = 16000
    pen: Anchor | None = None  # what the pen is pointing at right now


class VoiceStop(_M):
    type: Literal["voice.stop"] = "voice.stop"


class LineCorrection(_M):
    """ "Tell the tutor what you meant": what a line really says, typed (or dictated) by the student from a question
    chip or a "?" mark. Only the reading changes; the ink is never touched."""

    type: Literal["line.correction"] = "line.correction"
    page_id: str
    line_id: str
    text: str = Field(min_length=1, max_length=500)
    source: Literal["typed", "spoken"] = "typed"


class VoiceDictate(_M):
    """The next utterance fills the correction field for this line (it is not a question for the tutor)."""

    type: Literal["voice.dictate"] = "voice.dictate"
    page_id: str
    line_id: str
    cancel: bool = False  # the field was closed before anything was said


ClientMessage = Annotated[
    InkUpdate
    | HintRequest
    | QuestionAnswer
    | GhostAction
    | MarkDismiss
    | SessionUpdate
    | VoiceAudio
    | VoiceStop
    | LineCorrection
    | VoiceDictate,
    Field(discriminator="type"),
]


# ---------------------------------------------------------------- wire: server → client
class SessionMsg(_M):
    type: Literal["session"] = "session"
    session: Session
    tier2: TutorInfo


class TutorInfo(_M):
    provider: Provider
    model: str
    ready: bool
    detail: str = ""


class ReadingMsg(_M):
    type: Literal["reading"] = "reading"
    reading: Reading
    ms: float = 0  # tier-1 latency after the update arrived


class FeedbackMsg(_M):
    type: Literal["feedback"] = "feedback"
    feedback: FeedbackEvent
    provider: str = ""
    model: str = ""
    ms: float = 0  # since the triggering ink.update


Phase = Literal["received", "recognizing", "checking", "reviewing", "waiting", "idle", "cancelled", "error"]


class StatusMsg(_M):
    type: Literal["status"] = "status"
    page_id: str | None = None
    phase: Phase
    detail: str = ""
    line_ids: list[str] = []  # lines being checked (for the per-line shimmer)


class VoiceTranscript(_M):
    type: Literal["voice.transcript"] = "voice.transcript"
    text: str
    final: bool


class VoiceAudioOut(_M):
    type: Literal["voice.audio"] = "voice.audio"
    pcm16_b64: str
    sample_rate: int
    feedback_id: str | None = None


class ErrorMsg(_M):
    type: Literal["error"] = "error"
    message: str


MemoryLevel = Literal["ok", "low", "critical"]
# full: resident · offload: experts paged from the SSD (slower) · refused: not enough memory to load it ·
# external: a model server the runtime doesn't manage · loading · unavailable: not downloaded / failed · off
Tier2Mode = Literal["full", "offload", "refused", "external", "loading", "unavailable", "off"]


class RuntimeStatus(_M):
    available_gb: float  # what macOS can hand out without compressing or swapping (runtime/memory.py)
    level: MemoryLevel
    tier2_model: str
    tier2_mode: Tier2Mode
    detail: str = ""  # e.g. "offload (9 GB expert cache)" or why the model was refused
    event: str = ""  # the last runtime event, human-readable ("Memory low: tutor switched to slower mode")
    event_id: int = 0  # increments on every new event (the web shows each one once)


class RuntimeMsg(_M):
    type: Literal["runtime"] = "runtime"
    status: RuntimeStatus


ServerMessage = Annotated[
    SessionMsg | ReadingMsg | FeedbackMsg | StatusMsg | VoiceTranscript | VoiceAudioOut | ErrorMsg | RuntimeMsg,
    Field(discriminator="type"),
]


# ================================================================ screen tutor (docs/SCREEN-TUTOR.md)
# The macOS helper (apps/overlay) talks to /ws/screen/{id}, localhost only. Every screen-mode BBox and point is in
# global display points, AX coordinates: origin at the top-left of the main display (the one with the menu bar),
# y down. The helper converts to AppKit coordinates (bottom-left origin, y up) to draw.
# Phase 11 landed the overlay + phantom icon messages; Phases 12–13 add perception (AXSnapshot, ScreenFrame,
# InputEvent), voice over the same socket and the guide loop (GuidePlan, guide.status). The helper's Codable structs
# mirror these (apps/overlay/Sources/OverlayCore/Protocol.swift).
class Point2(_M):
    x: float
    y: float


OverlayEventType = Literal["highlight", "arrow", "caption", "pulse", "clear", "phantom_state"]
PhantomState = Literal["idle", "listening", "thinking", "speaking", "paused", "capture_paused"]


class OverlayEvent(_M):
    """One thing the helper draws over the screen (or clears). It points; nothing here can act on the screen."""

    id: str  # an event with the same id replaces the one already drawn
    type: OverlayEventType
    bbox: BBox | None = None  # highlight/pulse target, the arrow's head, the caption's anchor
    from_point: Point2 | None = None  # arrow tail (e.g. the pointer)
    text: str | None = None  # caption text
    step_id: str | None = None
    ttl_ms: int | None = Field(default=None, ge=0)  # removed after this long; None = until cleared
    refers_to: str | None = None  # clear/pulse: the event it acts on (a clear with neither this nor step_id clears all)
    state: PhantomState | None = None  # phantom_state only


GuidePhase = Literal["idle", "planning", "locating", "guiding", "verifying", "replanning", "paused", "done", "error"]


class AXElement(_M):
    """One accessibility element of the frontmost app (role/title/frame); its value is never sent for secure fields."""

    id: str
    role: str  # AXButton, AXMenuItem, AXTab, AXTextField, …
    title: str | None = None  # AXTitle, else AXDescription, else AXHelp
    value: str | None = None
    frame: BBox  # global points
    enabled: bool = True
    path: list[
        str
    ] = []  # role:title chain from the window, e.g. ["AXWindow:Book1", "AXTabGroup:", "AXRadioButton:Insert"]


class DoneWhen(_M):
    """How the stepper knows a step is done: state predicates first; the user's "next" always works."""

    kind: Literal["ax_exists", "ax_value", "window_title", "vlm_yes_no", "user_next"]
    args: dict[
        str, str
    ] = {}  # ax_exists: role/title_regex; ax_value: title_regex/value_regex; window_title: regex; vlm_yes_no: question


class Target(_M):
    ax_path: list[str] | None = None
    title_regex: str | None = None
    role: str | None = None
    description: str | None = None  # in words, for the VLM fallback ("the Insert tab of the ribbon")
    bbox: BBox | None = None  # set once located
    source: Literal["ax", "vlm"] | None = None  # None until located


class GuideStep(_M):
    id: str
    n: int
    instruction: str  # caption text
    speak: str  # ≤ 15 words, said aloud (code-enforced)
    target: Target | None = None
    done_when: DoneWhen
    hint_levels: list[str] = []  # rungs 1–3; rung 4 (the full sequence) is the plan itself, on request only


class GuidePlan(_M):
    goal: str
    app: str
    steps: list[GuideStep]  # ≤ 15 (code-enforced; longer goals are split)
    version: int = 0  # bumps on every replan


class DisplayInfo(_M):
    id: int  # CGDirectDisplayID
    frame: BBox  # the display's bounds in global points
    scale: float = 1  # backing pixels per point
    main: bool = False  # has the menu bar; global (0, 0) is its top-left


# ---------------------------------------------------------------- wire: helper → server
class ScreenHello(_M):
    """Sent on connect and whenever the display layout changes, so the server can keep boxes on screen."""

    type: Literal["screen.hello"] = "screen.hello"
    helper: str = ""  # e.g. "inktutor-overlay 0.1.0"
    displays: list[DisplayInfo] = []


class AXSnapshot(_M):
    """The frontmost app's visible elements. Empty (and secure_field_focused) while a password field has focus."""

    type: Literal["ax.snapshot"] = "ax.snapshot"
    app: str
    bundle_id: str = ""
    window_title: str = ""
    display: int = 0
    elements: list[AXElement] = []
    focused_id: str | None = None
    secure_field_focused: bool = False
    pointer: Point2 | None = None  # cursor at snapshot time (arrow when the target is > 300 pt away)
    t_ms: float


class ScreenFrame(_M):
    """A downscaled screenshot of one display. Never leaves the Mac (screen mode is local-only)."""

    type: Literal["screen.frame"] = "screen.frame"
    id: str
    display: int
    origin: Point2  # the display's top-left in global points
    width: int  # JPEG pixels
    height: int
    scale: float  # JPEG pixels per point
    jpeg_b64: str
    window: BBox | None = None  # frontmost window, global points
    t_ms: float
    trigger: Literal["tick", "click", "focus"]


class InputEvent(_M):
    """That the user clicked or typed, and where they clicked. Keys carry no content, not even key codes."""

    type: Literal["input.event"] = "input.event"
    kind: Literal["click", "key"]
    t_ms: float
    at: Point2 | None = None


class GuideGoal(_M):
    type: Literal["guide.goal"] = "guide.goal"
    text: str = Field(min_length=1, max_length=500)  # typed into the phantom icon's text box


class GuideControl(_M):
    type: Literal["guide.control"] = "guide.control"
    action: Literal["next", "back", "repeat", "pause", "resume", "stop"]  # phantom pause/exit, voice commands


ScreenClientMessage = Annotated[
    ScreenHello
    | ScreenFrame
    | AXSnapshot
    | InputEvent
    | GuideGoal
    | GuideControl
    | SessionUpdate  # session.update {voice}: phantom listen on/off
    | VoiceAudio
    | VoiceStop,
    Field(discriminator="type"),
]


# ---------------------------------------------------------------- wire: server → helper
class OverlayMsg(_M):
    type: Literal["overlay"] = "overlay"
    event: OverlayEvent
    provider: str = ""
    model: str = ""


class ScreenConfigMsg(_M):
    """Sent on connect. The helper skips blocklisted apps' windows before capture (Phase 12)."""

    type: Literal["screen.config"] = "screen.config"
    blocklist: list[str]
    capture_fps: float = 1.0
    allow_automation: bool = False  # not built; the helper has no code that clicks, types or scrolls


class GuidePlanMsg(_M):
    type: Literal["guide.plan"] = "guide.plan"
    plan: GuidePlan


class GuideStatusMsg(_M):
    """What the tutor is doing, for the phantom icon and its transcript ("Got it — planning…", "Step 2 of 5")."""

    type: Literal["guide.status"] = "guide.status"
    phase: GuidePhase
    step: int = 0
    of: int = 0
    detail: str = ""


ScreenServerMessage = Annotated[
    OverlayMsg
    | ScreenConfigMsg
    | GuidePlanMsg
    | GuideStatusMsg
    | VoiceTranscript
    | VoiceAudioOut
    | RuntimeMsg
    | ErrorMsg,
    Field(discriminator="type"),
]

SessionMsg.model_rebuild()
