"""Spoken hint-ladder commands → hint level (kept separate so it is testable without audio)."""


def next_level(cmd: str, current: int) -> int:
    """ "hint" climbs to at most level 2, "more" climbs one, "step" is level 3, "solution" level 4 (explicit)."""
    if cmd == "solution":
        return 4
    if cmd == "step":
        return 3
    if cmd == "more":
        return min(3, current + 1)
    return min(2, max(1, current + 1))
