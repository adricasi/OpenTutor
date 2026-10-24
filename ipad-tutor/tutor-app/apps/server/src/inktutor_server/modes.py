"""Modes as plugins: tutor-app/modes/<name>/{config.toml, rubric.md}. Adding a subject = adding a folder."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field

from .config import APP_ROOT

MODES_DIR = APP_ROOT / "modes"
ALIASES = {"diagram": "system_design", "other": "math"}


@dataclass(frozen=True)
class ModeConfig:
    name: str
    recognizer: str = "math"
    checkers: tuple[str, ...] = ()
    marks: tuple[str, ...] = ("check", "cross", "underline", "question")
    checker_marks: tuple[str, ...] = ()  # glyphs only the checkers place (a tutor ✗ becomes a question)
    divisions: bool = False
    ghost_unprompted: bool = False
    confidence_gate: float = 0.5
    settle_ms: int = 2500
    rubric: str = ""
    extra: dict[str, object] = field(default_factory=dict)


def load_mode(name: str) -> ModeConfig:
    key = name if (MODES_DIR / name).is_dir() else ALIASES.get(name, "math")
    d = MODES_DIR / key
    cfg = tomllib.loads((d / "config.toml").read_text()) if (d / "config.toml").exists() else {}
    rubric = (d / "rubric.md").read_text() if (d / "rubric.md").exists() else ""
    known = {
        "recognizer",
        "checkers",
        "marks",
        "checker_marks",
        "divisions",
        "ghost_unprompted",
        "confidence_gate",
        "settle_ms",
    }
    return ModeConfig(
        name=key,
        recognizer=cfg.get("recognizer", "math"),
        checkers=tuple(cfg.get("checkers", ())),
        marks=tuple(cfg.get("marks", ("check", "cross", "underline", "question"))),
        checker_marks=tuple(cfg.get("checker_marks", ())),
        divisions=bool(cfg.get("divisions", False)),
        ghost_unprompted=bool(cfg.get("ghost_unprompted", False)),
        confidence_gate=float(cfg.get("confidence_gate", 0.5)),
        settle_ms=int(cfg.get("settle_ms", 2500)),
        rubric=rubric,
        extra={k: v for k, v in cfg.items() if k not in known},
    )
