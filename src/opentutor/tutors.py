"""Subject tutors as folders: tutors/<id>/tutor.toml + rubric.md (REQ-008, D-5)."""

from __future__ import annotations

import logging
import re
import tomllib
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

log = logging.getLogger(__name__)

RUBRIC_SECTIONS = ("teaching", "check", "practice")


class Example(BaseModel):
    model_config = ConfigDict(extra="forbid")
    student: str
    tutor: str


class TutorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-z0-9-]{1,40}$")
    name: str = Field(min_length=1, max_length=40)
    description: str = Field(min_length=1, max_length=120)
    enabled: bool = True
    checker: str | None = None
    topics: list[str] = Field(min_length=1)
    examples: list[Example] = []


class Tutor(BaseModel):
    config: TutorConfig
    rubric: dict[str, str]

    @property
    def id(self) -> str:
        return self.config.id


class NoTutorsError(Exception):
    pass


def parse_sections(markdown: str) -> dict[str, str]:
    """Split markdown into `## heading` sections (pattern from ipad-tutor tutor/base.py)."""
    sections: dict[str, str] = {}
    for match in re.finditer(r"^## +(\w+)\s*\n(.*?)(?=^## |\Z)", markdown, re.M | re.S):
        sections[match.group(1).lower()] = match.group(2).strip()
    return sections


def load_tutor(folder: Path) -> Tutor:
    config = TutorConfig.model_validate(tomllib.loads((folder / "tutor.toml").read_text()))
    if config.id != folder.name:
        raise ValueError(f"id {config.id!r} must match the folder name {folder.name!r}")
    rubric = parse_sections((folder / "rubric.md").read_text())
    missing = [name for name in RUBRIC_SECTIONS if name not in rubric]
    if missing:
        raise ValueError(f"rubric.md is missing sections: {', '.join(missing)}")
    return Tutor(config=config, rubric=rubric)


def load_tutors(tutors_dir: Path) -> dict[str, Tutor]:
    tutors: dict[str, Tutor] = {}
    for folder in sorted(p for p in tutors_dir.iterdir() if p.is_dir()):
        try:
            tutor = load_tutor(folder)
        except (OSError, ValueError, ValidationError, tomllib.TOMLDecodeError) as error:
            log.error("skipping tutor %s: %s", folder, error)
            continue
        if tutor.config.enabled:
            tutors[tutor.id] = tutor
    if not tutors:
        raise NoTutorsError(
            f"No valid, enabled subject tutor in {tutors_dir}. "
            "Each tutor needs a folder with tutor.toml and rubric.md."
        )
    return tutors
