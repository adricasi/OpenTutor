"""What a spoken utterance means in screen mode (EN / ES / CA), without asking a language model.

    controls    next · back · repeat · stop            → the same actions as `guide.control`
    ladder      why (rung 2) · what does this do (3) · show me all the steps (4, explicit) · hint / more
    otherwise   None: a new goal if there is no plan, else a question about the current step

Controls only match short whole utterances (≤ 4 words besides "please"/"ok"), so "what's the next thing in the
Insert menu" is a question, not "next". Spanish "para" alone is not "stop" (it also means "for"): only "stop",
"para la guía", "parar", "detente", "atura", "prou". The canvas ladder words (`voice.session.parse_command`) climb
the rungs here too, but only after the screen commands, so "next step" is `next`, not a hint.
"""

from __future__ import annotations

import re

from ..ax.search import strip_accents
from ..voice.session import parse_command

POLITE = re.compile(
    r"\b(please|por favor|si us plau|ok|okay|vale|thanks|gracias|gracies|merci|now|ahora|ara|tutor|hey|oye|ei)\b"
)
CONTROLS: list[tuple[str, re.Pattern[str]]] = [
    (
        "stop",
        re.compile(
            r"^(stop( it| the guide| guiding)?|cancel|quit|exit|para la guia|parar|detente|deten la guia|"
            r"cancela|atura( la guia)?|atura't|aturat|prou)$"
        ),
    ),
    (
        "next",
        re.compile(
            r"^(next( step| one)?|go on|continue|carry on|done|i did it|i'm done|siguiente( paso)?|el siguiente|"
            r"sigue|continua|listo|hecho|ya esta|ya|seguent( pas)?|endavant|fet|ja esta)$"
        ),
    ),
    (
        "back",
        re.compile(
            r"^((go )?back|previous( step)?|step back|atras|vuelve( atras)?|paso anterior|anterior|"
            r"enrere|torna( enrere)?|pas anterior)$"
        ),
    ),
    (
        "repeat",
        re.compile(
            r"^(repeat( that| it)?|say (that|it) again|again|come again|repite(lo)?|otra vez|de nuevo|"
            r"repeteix(-ho)?|una altra vegada|un altre cop)$"
        ),
    ),
    ("pause", re.compile(r"^(pause|wait|pausa|espera|espera't)$")),
    ("resume", re.compile(r"^(resume|go ahead|sigue adelante|reanuda|continua endavant)$")),
]
LADDER: list[tuple[str, re.Pattern[str]]] = [
    (
        "all_steps",
        re.compile(
            r"\b(all (the )?steps|every step|whole (sequence|thing)|the full list|todos los pasos|"
            r"tots els passos|toda la secuencia)\b"
        ),
    ),
    (
        "what",
        re.compile(
            r"^(what does (this|that|it) do|what is (this|that)|what's (this|that)|que hace (esto|eso)|"
            r"que es (esto|eso)|que fa (aixo|aquest)|que es aixo|per a que serveix|para que sirve)$"
        ),
    ),
    ("why", re.compile(r"^(why|why\?|but why|why that|por que|y por que|per que|perque|i per que|what for|para que)$")),
]


def clean(text: str) -> str:
    t = strip_accents(text).lower().replace("’", "'")
    t = re.sub(r"[¿¡.,!?;:\"]", " ", t)
    t = POLITE.sub(" ", t)
    return " ".join(t.split())


def parse_screen_command(text: str) -> str | None:
    """`next` `back` `repeat` `stop` `pause` `resume` · `why` `what` `all_steps` · `hint` `more` `step` `solution`."""
    t = clean(text)
    if not t:
        return None
    words = t.split()
    if len(words) <= 4:
        for name, pat in CONTROLS:
            if pat.match(t):
                return name
    for name, pat in LADDER:
        if ((name == "all_steps" and len(words) <= 9) or len(words) <= 6) and pat.search(t):
            return name
    if len(words) <= 5:  # the canvas ladder words: "a hint", "more", "show me the step", "the solution"
        cmd = parse_command(text)  # its word list has the accented spellings
        if cmd is not None:
            return cmd
    return None


def rung_for(cmd: str, current: int) -> int:
    """Hint-ladder rung for a spoken ladder command (1 location · 2 why · 3 what it does · 4 the whole sequence)."""
    if cmd in ("all_steps", "solution"):
        return 4
    if cmd in ("what", "step"):
        return 3
    if cmd == "why":
        return 2
    return min(3, max(2, current + 1))  # "hint"/"more": the location is already on screen, so the first is "why"
