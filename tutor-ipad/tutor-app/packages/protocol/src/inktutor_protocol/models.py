"""The one contract between server and web (ARCHITECTURE §2). Field names are normative.

Change a type here → `make protocol` regenerates `schema.json` and the TS types → then touch server and web.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

# (t_ms, x, y, pressure, tilt_x, tilt_y) — CSS px of the page, t relative to session start.
Point = tuple[float, float, float, float, float, float]
PointerType = Literal["pen", "mouse", "touch"]
Mode = Literal["math", "diagram", "system_design", "consulting", "language", "accounting", "sudoku", "other"]
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


FeedbackType = Literal["mark", "ghost", "hint", "question", "speak", "explain", "summary", "retract"]
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


class CheckResult(_M):
    ok: bool
    kind: str  # e.g. "math.equivalent", "arithmetic.long_division"
    anchor: Anchor
    detail: str
    expected: str | None = None  # what the checker computed (never shown unprompted)


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

SessionMsg.model_rebuild()
