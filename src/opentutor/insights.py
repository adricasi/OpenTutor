"""Teacher insights: aggregated counts only, no text, images or nicknames (REQ-013)."""

from __future__ import annotations

import hmac
import secrets
import time
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field

MAX_TRIES = 5
LOCKOUT_S = 5 * 60


@dataclass
class TutorStats:
    topics: Counter[str] = field(default_factory=Counter)
    hints: int = 0
    verdicts: Counter[str] = field(default_factory=Counter)
    struggles: Counter[str] = field(default_factory=Counter)  # "not yet correct" per topic


class Insights:
    def __init__(self) -> None:
        self.by_tutor: defaultdict[str, TutorStats] = defaultdict(TutorStats)

    def exercise(self, tutor_id: str, topic: str) -> None:
        self.by_tutor[tutor_id].topics[topic] += 1

    def hint(self, tutor_id: str) -> None:
        self.by_tutor[tutor_id].hints += 1

    def verdict(self, tutor_id: str, topic: str, verdict: str) -> None:
        stats = self.by_tutor[tutor_id]
        stats.verdicts[verdict] += 1
        if verdict == "not_yet_correct":
            stats.struggles[topic] += 1


class TeacherGate:
    """PIN printed on the operator console; 5 wrong tries lock that address for 5 minutes."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self.pin = f"{secrets.randbelow(1_000_000):06d}"
        self.token = secrets.token_urlsafe(32)
        self._failures: dict[str, tuple[int, float]] = {}
        self._now = clock

    def locked(self, address: str) -> bool:
        count, until = self._failures.get(address, (0, 0.0))
        return count >= MAX_TRIES and self._now() < until

    def login(self, address: str, pin: str) -> bool:
        if self.locked(address):
            return False
        if hmac.compare_digest(pin.strip(), self.pin):
            self._failures.pop(address, None)
            return True
        count, _ = self._failures.get(address, (0, 0.0))
        if count >= MAX_TRIES:  # lockout over: start counting again
            count = 0
        self._failures[address] = (count + 1, self._now() + LOCKOUT_S)
        return False

    def is_teacher(self, cookie: str | None) -> bool:
        return bool(cookie) and hmac.compare_digest(cookie or "", self.token)
