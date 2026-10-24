"""Tier 2: one `Tutor` interface, three providers (local MLX, Anthropic, OpenAI) — non-negotiable 3.

Providers stream JSON Lines; `JsonLines` turns the raw text stream into dicts as each line completes,
so the agent can validate and emit a mark the moment it arrives. Prompts live in prompts/<mode>.md.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

PROMPTS = Path(__file__).parent / "prompts"


@dataclass
class LineContext:
    id: str
    latex: str | None
    text: str | None
    kind: str
    confidence: float
    checks: list[str] = field(default_factory=list)  # deterministic verdicts, human-readable
    checker_ok: bool | None = None  # None: no checker could decide


@dataclass
class ReviewContext:
    mode: str
    problem: str
    rubric: str
    lines: list[LineContext]  # only settled, confident lines
    changed: list[str]  # ids of lines that changed since the last review
    images: list[tuple[str, bytes]]  # (line id, PNG) crops of the changed lines
    prior: list[str]  # feedback already on the page (so the tutor doesn't re-mark)
    dismissed: list[str]  # what the user dismissed (don't repeat)
    ghost_unprompted: bool = False
    graph: dict[str, Any] | None = None  # diagram modes: nodes/edges with ids
    history: list[str] = field(default_factory=list)  # work the student wiped earlier ("keep page history")


@dataclass
class HintContext:
    mode: str
    problem: str
    rubric: str
    line: LineContext
    level: int  # 1 nudge · 2 hint · 3 corrected step · 4 full solution (explicit only)
    image: bytes | None
    previous_hints: list[str]
    target_text: str = ""  # the mark the user tapped
    history: list[str] = field(default_factory=list)  # work the student wiped earlier ("keep page history")


@dataclass
class VoiceContext:
    mode: str
    problem: str
    rubric: str
    transcript: str
    pen_line: LineContext | None
    lines: list[LineContext]


@dataclass
class SummaryContext:
    """Teacher view: the learning log only (progress.py), never images or strokes."""

    mode: str
    problem: str
    student: str
    log: dict[str, Any]  # LearningLog, trimmed to what the summary needs


@dataclass
class PlanContext:
    """Screen tutor (apps/overlay + screen/): a goal → steps over the user's frontmost app. Local only."""

    goal: str
    app: str
    window_title: str
    ax_summary: str  # `role | title | value | path tail`, one element per line (ax.AXIndex.summary)
    image: bytes | None = None  # JPEG of the frontmost window, ≤ 1024 px
    image_size: tuple[int, int] | None = None
    recipe: str = ""  # modes/apps/<app>.md, when the app has one
    done: list[str] = field(default_factory=list)  # steps already done (a replan keeps the goal)
    note: str = ""  # why this is a replan ("you switched to Numbers")
    max_steps: int = 15


@dataclass
class LocateContext:
    """Where a described control is in a screenshot (`{"type":"bbox"}` in image pixels), or a yes/no question
    about it (`{"type":"yes_no"}`) for `vlm_yes_no`."""

    app: str
    window_title: str
    description: str  # the target in words + the step's instruction
    image: bytes  # JPEG
    image_size: tuple[int, int]
    question: str | None = None  # set: answer yes/no instead of locating
    recipe: str = ""


@dataclass
class ExplainContext:
    """A spoken question or a hint-ladder rung about the current step (rung 2 why · 3 what it does · 4 all steps)."""

    goal: str
    app: str
    window_title: str
    step: str  # the current step's instruction ("" when there is no plan yet)
    steps: list[str]  # the whole plan, in order
    level: int  # 2, 3, 4; 0 = answer the question
    question: str = ""
    recipe: str = ""


class Tutor(Protocol):
    name: str  # "local" | "anthropic" | "openai"
    model: str

    def review(self, ctx: ReviewContext) -> AsyncIterator[dict[str, Any]]: ...
    def hint(self, ctx: HintContext) -> AsyncIterator[dict[str, Any]]: ...
    def converse(self, ctx: VoiceContext) -> AsyncIterator[dict[str, Any]]: ...
    def summarize(self, ctx: SummaryContext) -> AsyncIterator[dict[str, Any]]: ...  # {"type":"summary","text":…}
    # screen tutor: steps as `{"type":"step",…}` lines; a bbox or yes/no; `{"type":"speak","text":…}`
    def plan(self, ctx: PlanContext) -> AsyncIterator[dict[str, Any]]: ...
    def locate(self, ctx: LocateContext) -> AsyncIterator[dict[str, Any]]: ...
    def explain(self, ctx: ExplainContext) -> AsyncIterator[dict[str, Any]]: ...


def summary_prompt() -> str:
    """prompts/progress.md, section `## summary` (no tutoring rules: this one writes for the teacher)."""
    return _sections((PROMPTS / "progress.md").read_text()).get("summary", "")


def load_prompt(mode: str, kind: str) -> str:
    """prompts/<mode>.md (falls back to shared.md), section `## <kind>`; shared rules are always included."""
    shared = (PROMPTS / "shared.md").read_text()
    f = PROMPTS / f"{mode}.md"
    text = f.read_text() if f.exists() else ""
    sections = _sections(shared) | _sections(text)
    # voice answers get their own short rules (the marking rules pulled them towards vague nudges)
    rules = sections.get("converse_rules", "") if kind == "converse" else sections.get("rules", "")
    body = sections.get(kind, "")
    return f"{rules}\n\n{body}".strip()


def _sections(md: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for m in re.finditer(r"^## (\w+)\s*\n(.*?)(?=^## |\Z)", md, re.M | re.S):
        out[m.group(1).lower()] = m.group(2).strip()
    return out


# A backslash models forgot to escape: anything JSON doesn't allow, plus \f \b \n \r \t followed by a letter,
# which is LaTeX (\frac, \times, \neq, \beta), not a control character. Escaped pairs (\\) are left alone.
_LATEX_ESCAPE = re.compile(r'\\(\\|[bfnrt](?=[a-z])|u(?![0-9a-fA-F]{4})|[^"\\/bfnrtu])')


def _escape_latex(s: str) -> str:
    return _LATEX_ESCAPE.sub(lambda m: m.group(0) if m.group(1) == "\\" else "\\" + m.group(0), s)


def loads_lenient(line: str) -> dict[str, Any] | None:
    """Parse one JSON object; tolerate code fences and LaTeX backslashes models forget to escape."""
    s = line.strip().strip(",")
    if s.startswith("```") or not s.startswith("{"):
        return None
    for candidate in (_escape_latex(s), s):
        try:
            v = json.loads(candidate)
            return v if isinstance(v, dict) else None
        except json.JSONDecodeError:
            continue
    return None


_PARTIAL_SPEAK = re.compile(r'^\s*\{.*?"type"\s*:\s*"speak".*?"text"\s*:\s*"((?:[^"\\]|\\.)*)(")?', re.S)
_SENTENCE_END = re.compile(r"[.!?;:](?=\s)")


def _partial_string(raw: str) -> str:
    for candidate in (_escape_latex(raw), raw):
        try:
            return str(json.loads(f'"{candidate}"'))
        except json.JSONDecodeError:
            continue
    return raw


class JsonLines:
    """Feed text chunks; get complete JSON objects out as soon as their line ends.

    While a `speak` line is still streaming, each newly completed sentence of its text also comes out as
    `{"type": "speak_partial", "text": <the text so far, up to that sentence end>}`, so voice can start
    talking before the line ends. Consumers that don't speak ignore the unknown type.
    """

    def __init__(self) -> None:
        self.buf = ""
        self._partial = 0  # length of the partial speak text already emitted for the current line

    def feed(self, chunk: str) -> list[dict[str, Any]]:
        self.buf += chunk
        out = []
        while "\n" in self.buf:
            line, self.buf = self.buf.split("\n", 1)
            self._partial = 0
            obj = loads_lenient(line)
            if obj is not None:
                out.append(obj)
        if (m := _PARTIAL_SPEAK.match(self.buf)) is not None:
            text = _partial_string(m.group(1))
            ends = [len(text)] if m.group(2) else [e.end() for e in _SENTENCE_END.finditer(text)]  # closed: all of it
            if ends and ends[-1] > self._partial:
                self._partial = ends[-1]
                out.append({"type": "speak_partial", "text": text[: ends[-1]]})
        return out

    def close(self) -> list[dict[str, Any]]:
        obj = loads_lenient(self.buf)
        self.buf = ""
        return [obj] if obj else []
