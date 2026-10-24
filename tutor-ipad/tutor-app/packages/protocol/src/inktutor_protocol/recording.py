"""Recordings (`inktutor.recording/1`, docs/RECORDINGS.md) and their sidecar annotations.

Recordings are read-only test data. Ground truth (drafted automatically, confirmed by a person)
lives in `fixtures/annotations/<recording stem>.json` and is merged on load, so the raw file never changes.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .models import Mode, Point

CheckpointKind = Literal["step", "mistake", "final", "question", "ambiguous"]


class _R(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)


class RecStroke(_R):
    id: str
    page: str | None = None
    color: str = "#1F2328"
    width: float = 3
    pointer_type: str = Field("pen", alias="pointerType")
    pts: list[Point]


class RecPage(_R):
    id: str
    index: int = 0
    width: float
    height: float
    dpr: float = 1
    strokes: list[RecStroke] = []


class RecDevice(_R):
    dpr: float = 1
    canvas_width: float = Field(0, alias="canvasWidth")
    canvas_height: float = Field(0, alias="canvasHeight")


class RecSession(_R):
    id: str
    title: str = ""
    mode: str = "other"
    problem: str = ""
    intent: str = ""
    duration_ms: float = Field(0, alias="durationMs")
    device: RecDevice | None = None


class Checkpoint(_R):
    id: str
    t: float
    page: str
    stroke_ids: list[str] = Field(default_factory=list, alias="strokeIds")
    kind: CheckpointKind = "step"
    snapshot: str | None = None
    truth: str = ""
    expect: str = ""
    note: str = ""
    drafted_by: str | None = Field(None, alias="draftedBy")
    confirmed: bool = False  # set by a person after checking a drafted field
    lines: list[str] = []  # optional: which truth lines this checkpoint is about (region ids in annotations)


class Photo(_R):
    id: str
    t: float = 0
    page: str | None = None
    width: int = 0
    height: int = 0
    data_url: str = Field("", alias="dataUrl")
    truth: str = ""
    expect: str = ""
    note: str = ""


class AudioClip(_R):
    """What the student said while writing (recorder Mic button): times on the same clock as the strokes."""

    id: str
    t: float = 0
    t_end: float | None = Field(None, alias="tEnd")
    page: str | None = None
    mime: str = "audio/webm"
    bytes_: int = Field(0, alias="bytes")
    data_url: str = Field("", alias="dataUrl")


class Stats(_R):
    strokes: int = 0
    points: int = 0
    checkpoints: int = 0
    photos: int = 0
    pages: int = 0
    audio: int = 0
    audio_ms: int = Field(0, alias="audioMs")


class Recording(_R):
    schema_: str = Field(alias="schema")
    session: RecSession
    pages: list[RecPage]
    events: list[dict[str, object]] = []
    checkpoints: list[Checkpoint] = []
    photos: list[Photo] = []
    audio: list[AudioClip] = []
    stats: Stats = Field(default_factory=lambda: Stats.model_validate({}))
    # filled from the sidecar (not part of the raw file)
    mode_override: Mode | None = None
    problem_override: str | None = None

    @property
    def mode(self) -> Mode:
        if self.mode_override:
            return self.mode_override
        m = self.session.mode
        return m if m in ("math", "diagram", "language", "accounting", "other") else "other"  # type: ignore[return-value]


class Annotations(_R):
    """Sidecar for one recording: corrections + drafted ground truth. A reviewer sets `confirmed` per checkpoint."""

    recording: str  # file name of the recording
    mode: Mode | None = None
    problem: str | None = None
    note: str = ""
    checkpoints: list[Checkpoint] = []
    photos: dict[str, Photo] = {}  # photo id → truth/expect/note
