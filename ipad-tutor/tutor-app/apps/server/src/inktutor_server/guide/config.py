"""The guide loop's knobs (`modes/screen/config.toml`) and the app recipes (`modes/apps/<app>.md`)."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, fields
from pathlib import Path

from ..ax.search import norm
from ..modes import MODES_DIR

SCREEN_DIR = MODES_DIR / "screen"
APPS_DIR = MODES_DIR / "apps"


@dataclass(frozen=True)
class GuideConfig:
    capture_fps: float = 1.0
    arrow_min_pt: float = 300
    pulse_idle_s: float = 8
    verify_debounce_ms: float = 300
    vlm_min_interval_s: float = 2
    dhash_threshold: int = 4
    locate_give_up_s: float = 6
    max_steps: int = 15
    # a pause mid-sentence splits what you say: wait this long after a sentence for more before acting (a
    # command like "siguiente" acts at once); speech starting again within it joins the pieces
    utterance_hold_s: float = 0.7
    click_margin_pt: float = 6.0  # a click this close to the highlighted box counts as clicking it
    click_done_s: float = 0.4  # …and completes the step this long after the click
    max_continues: int = 3  # plan ran out but the goal isn't visibly done: plan the next steps, at most this often
    max_speak_words: int = 15
    ax_summary_max: int = 70  # named, clickable first; ~1k tokens: fewer to read before the first step
    plan_image_max_px: int = 1024
    replan_confirm_snapshots: int = 2
    ok_caption_ms: int = 1200
    tick_s: float = 0.25  # how often the loop looks at timers (pulse, locate timeout, VLM retry)


def load_config(path: Path | None = None) -> GuideConfig:
    f = path or SCREEN_DIR / "config.toml"
    raw = tomllib.loads(f.read_text()) if f.exists() else {}
    known = {x.name: x.type for x in fields(GuideConfig)}
    kw = {k: (int(v) if known[k] == "int" else float(v)) for k, v in raw.items() if k in known}
    return GuideConfig(**kw)  # type: ignore[arg-type]


def recipe_for(app: str, bundle_id: str = "", apps_dir: Path | None = None, goal: str = "") -> str:
    """The recipe whose file name appears in the app's name or bundle id ("excel" ⊂ "Microsoft Excel"): its general
    sections always, a task section (`## …` followed by a `keywords: a, b` line) only if the goal mentions a keyword."""
    d = apps_dir or APPS_DIR
    if not d.is_dir():
        return ""
    name, bundle = norm(app), bundle_id.lower()
    for f in sorted(d.glob("*.md")):
        key = norm(f.stem)
        if key and (key in name or key in bundle):
            return _sections_for(f.read_text(), norm(goal))
    return ""


def _close(w: str, kw: str) -> bool:
    """Same word, or one wrong letter in a word of 5+ letters (speech: "bivot" for "pivot")."""
    return w == kw or (len(kw) >= 5 and len(w) == len(kw) and sum(a != b for a, b in zip(w, kw, strict=True)) <= 1)


def _fuzzy_in(keyword: str, goal: str) -> bool:
    """`keyword` in `goal`, tolerating one wrong letter per long word (speech: "bivot table" → pivot table)."""
    if keyword in goal:
        return True
    words = goal.split()
    return all(any(_close(w, kw) for w in words) for kw in keyword.split())


def _sections_for(text: str, goal: str) -> str:
    head, *parts = text.split("\n## ")
    keep = [head]
    for part in parts:
        lines = part.split("\n")
        kw_line = lines[1] if len(lines) > 1 and lines[1].lower().startswith("keywords:") else None
        if kw_line is None:
            keep.append(part)
            continue
        words = [norm(w) for w in kw_line.split(":", 1)[1].split(",") if w.strip()]
        if any(w and _fuzzy_in(w, goal) for w in words):
            keep.append("\n".join([lines[0], *lines[2:]]))
    return "\n## ".join(keep)
