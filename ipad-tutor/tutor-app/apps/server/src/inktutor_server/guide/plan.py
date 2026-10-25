"""The planner's JSON lines → validated `GuideStep`s (rules in code, not prompt).

- at most `max_steps` steps (the rest of a longer plan is dropped; the goal should be split);
- `speak` ≤ `max_speak_words` words (cut), `instruction` ≤ 30 words, both non-empty;
- `done_when.kind` one of the five kinds (else `user_next`), its args strings (a predicate that is already true
  for the step's own target is accepted only after it was false once: see `guide.loop.Guide`);
- no step that types into a password field (target role AXSecureTextField or a password-like title).
"""

from __future__ import annotations

import logging
import re
from typing import Any

from inktutor_protocol import DoneWhen, GuideStep, Target
from pydantic import ValidationError

log = logging.getLogger("inktutor.guide")

DONE_KINDS = {"ax_exists", "ax_value", "window_title", "vlm_yes_no", "user_next"}
SECRET = re.compile(r"password|passcode|contrase|contrasenya|clave de acceso|\bpin\b|passwort|mot de passe", re.I)
MAX_INSTRUCTION_WORDS = 30


def cap_words(text: str, n: int) -> str:
    words = str(text).split()
    if len(words) <= n:
        return " ".join(words)
    return " ".join(words[:n]).rstrip(",;:") + "…"


def _strs(d: Any) -> dict[str, str]:
    return {str(k): str(v) for k, v in d.items() if v is not None} if isinstance(d, dict) else {}


def parse_step(obj: dict[str, Any], n: int, prefix: str = "p", max_speak_words: int = 15) -> GuideStep | None:
    """One `{"type":"step",…}` line → a GuideStep numbered `n` (1-based, id `<prefix>s<n>`), or None."""
    if obj.get("type") not in ("step", None):
        return None
    instruction = cap_words(str(obj.get("instruction") or obj.get("text") or "").strip(), MAX_INSTRUCTION_WORDS)
    speak = cap_words(str(obj.get("speak") or instruction).strip(), max_speak_words)
    if not instruction:
        return None
    raw_target = obj.get("target")
    target = None
    if isinstance(raw_target, dict):
        path = raw_target.get("ax_path")
        target = Target(
            title_regex=str(raw_target["title_regex"]) if raw_target.get("title_regex") else None,
            role=str(raw_target["role"]) if raw_target.get("role") else None,
            ax_path=[str(p) for p in path] if isinstance(path, list) and path else None,
            description=str(raw_target["description"]) if raw_target.get("description") else None,
        )
        if not (target.title_regex or target.ax_path or target.description):
            target = None
    dw_raw: dict[str, Any] = obj["done_when"] if isinstance(obj.get("done_when"), dict) else {}
    kind = str(dw_raw.get("kind") or "user_next")
    if kind not in DONE_KINDS:
        log.info("step %d: unknown done_when %r → user_next", n, kind)
        kind = "user_next"
    try:
        dw = DoneWhen(kind=kind, args=_strs(dw_raw.get("args")))  # type: ignore[arg-type]
        hints = [cap_words(str(h), 40) for h in obj.get("hint_levels") or [] if str(h).strip()][:3]
        step = GuideStep(
            id=f"{prefix}s{n}",
            n=n,
            instruction=instruction,
            speak=speak,
            target=target,
            done_when=dw,
            hint_levels=hints,
        )
    except ValidationError as e:
        log.info("step %d dropped: %s", n, e.errors()[0]["msg"])
        return None
    if types_a_secret(step):
        log.info("step %d dropped: it would type into a password field", n)
        return None
    return step


def types_a_secret(step: GuideStep) -> bool:
    t = step.target
    named = f"{t.title_regex or ''} {t.description or ''}" if t is not None else ""
    if t is not None and (t.role == "AXSecureTextField" or SECRET.search(named)):
        return True
    return bool(SECRET.search(step.instruction))
