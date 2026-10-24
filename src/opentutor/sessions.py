"""In-memory sessions (ADR-0005, REQ-001, REQ-011). Nothing here is ever written to disk."""

from __future__ import annotations

import re
import secrets
import time
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass, field

NICKNAME_RE = re.compile(r"^[A-Za-z0-9 _-]{1,20}$")


class JoinError(Exception):
    def __init__(self, code: str, message: str, status: int) -> None:
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


@dataclass
class Turn:
    role: str  # "student" | "tutor"
    text: str


@dataclass
class Exercise:
    text: str
    image: bytes | None = None
    topic: str = "other"
    typed: bool = True  # False when the text was read from a photo by the model


@dataclass
class Conversation:
    tutor_id: str
    exercise: Exercise | None = None
    turns: list[Turn] = field(default_factory=list)
    hint_level: int = 0
    practice: list[str] = field(default_factory=list)


@dataclass
class Session:
    id: str
    nickname: str
    address: str
    last_active: float
    conversation: Conversation | None = None
    in_flight_since: float | None = None  # set when a request is accepted (AC-009.3)
    ended: bool = False  # set on Finish or expiry; waiting requests leave the queue


def validate_nickname(raw: str) -> str:
    nickname = raw.strip()
    if not NICKNAME_RE.match(nickname) or not re.search(r"[A-Za-z0-9]", nickname):
        raise JoinError(
            "invalid_nickname",
            "Use 1-20 letters, digits, spaces, hyphens or underscores.",
            400,
        )
    return nickname


class SessionStore:
    def __init__(
        self,
        max_sessions: int,
        max_per_address: int,
        attempts_per_minute: int,
        idle_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._sessions: dict[str, Session] = {}
        self._attempts: defaultdict[str, deque[float]] = defaultdict(deque)
        self.max_sessions = max_sessions
        self.max_per_address = max_per_address
        self.attempts_per_minute = attempts_per_minute
        self.idle_seconds = idle_seconds
        self._now = clock

    def __len__(self) -> int:
        return len(self._sessions)

    def join(self, raw_nickname: str, address: str) -> Session:
        self.expire()
        now = self._now()
        attempts = self._attempts[address]
        while attempts and now - attempts[0] > 60:
            attempts.popleft()
        attempts.append(now)
        if len(attempts) > self.attempts_per_minute:
            raise JoinError("too_many_attempts", "Too many tries. Please wait a minute.", 429)
        nickname = validate_nickname(raw_nickname)
        if len(self._sessions) >= self.max_sessions:
            raise JoinError("hub_full", "The hub is full, try again in a few minutes.", 503)
        if sum(s.address == address for s in self._sessions.values()) >= self.max_per_address:
            raise JoinError("too_many_sessions", "This device already has sessions open.", 429)
        if any(s.nickname.lower() == nickname.lower() for s in self._sessions.values()):
            raise JoinError("nickname_taken", "That nickname is in use. Choose another one.", 409)
        session = Session(secrets.token_urlsafe(32), nickname, address, now)
        self._sessions[session.id] = session
        return session

    def get(self, session_id: str | None) -> Session | None:
        if not session_id:
            return None
        self.expire()
        session = self._sessions.get(session_id)
        if session:
            session.last_active = self._now()
        return session

    def end(self, session_id: str) -> None:
        session = self._sessions.pop(session_id, None)
        if session:
            session.ended = True

    def expire(self) -> None:
        cutoff = self._now() - self.idle_seconds
        for sid in [sid for sid, s in self._sessions.items() if s.last_active < cutoff]:
            self._sessions.pop(sid).ended = True
