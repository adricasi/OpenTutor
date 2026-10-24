"""The connected overlay helpers, per screen session id, and the rules an `OverlayEvent` must pass before it is sent.

One hub per app (`app.state.screen_hub`). A helper connects to `/ws/screen/{sid}`; the hub owns one `ScreenSession`
per id (perception, voice, the guide loop), created with the first helper and closed with the last. Whatever a
helper sends is routed to that session; the small messages (hello, goals, controls, listen on/off) are also kept in
a short inbox for `GET /api/screen/{sid}` (frames, snapshots, audio and input events are not: they are large or
frequent, and never logged). Server messages reach every helper connected under that id.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from inktutor_protocol import BBox, DisplayInfo, OverlayEvent, OverlayMsg, Point2, ScreenHello
from pydantic import BaseModel

if TYPE_CHECKING:
    from .session import ScreenSession

log = logging.getLogger("inktutor.screen")

INBOX = 50  # messages kept per session (newest last)
BULKY = {"screen.frame", "ax.snapshot", "voice.audio", "input.event"}  # not kept in the inbox, logged at debug
QUEUE = 256  # outgoing messages buffered per helper; a stuck helper loses the oldest
EDGE_PT = 1.0  # tolerance for "inside the display" (AX frames are rounded to whole points)


@dataclass(eq=False)
class Helper:
    sid: str
    out: asyncio.Queue[BaseModel] = field(default_factory=lambda: asyncio.Queue(QUEUE))
    hello: ScreenHello | None = None
    connected_at: float = field(default_factory=time.time)

    def send(self, msg: BaseModel) -> None:
        if self.out.full():
            self.out.get_nowait()
            log.warning("screen %s: helper is not reading; dropped the oldest message", self.sid)
        self.out.put_nowait(msg)


class ScreenHub:
    def __init__(self) -> None:
        self.helpers: dict[str, list[Helper]] = {}
        self.inbox: dict[str, deque[dict[str, Any]]] = {}
        self.sessions: dict[str, ScreenSession] = {}
        self.make_session: Any = None  # (sid) -> ScreenSession; set by routes.install
        self.cfg: Any = None  # guide.config.GuideConfig (modes/screen/config.toml); tests may replace it

    def session(self, sid: str) -> ScreenSession | None:
        """This id's screen session, created with its first helper."""
        if sid not in self.sessions and self.make_session is not None:
            self.sessions[sid] = self.make_session(sid)
        return self.sessions.get(sid)

    # ---- connections
    def connect(self, sid: str) -> Helper:
        h = Helper(sid)
        self.helpers.setdefault(sid, []).append(h)
        log.info("screen %s: helper connected (%d)", sid, len(self.helpers[sid]))
        return h

    def disconnect(self, h: Helper) -> None:
        live = self.helpers.get(h.sid, [])
        if h in live:
            live.remove(h)
        if not live:
            self.helpers.pop(h.sid, None)
            if (s := self.sessions.pop(h.sid, None)) is not None:
                s.close()  # the helper is gone: stop the guide, the voice and any tier-2 call
        log.info("screen %s: helper disconnected (%d left)", h.sid, len(live))

    def connected(self, sid: str) -> int:
        return len(self.helpers.get(sid, []))

    def displays(self, sid: str) -> list[DisplayInfo]:
        """The display layout from the newest hello of any helper connected under `sid`."""
        hellos = [h.hello for h in self.helpers.get(sid, []) if h.hello is not None]
        return hellos[-1].displays if hellos else []

    # ---- helper → server
    def receive(self, h: Helper, msg: BaseModel) -> None:
        if isinstance(msg, ScreenHello):
            h.hello = msg
        kind = getattr(msg, "type", type(msg).__name__)
        if kind in BULKY:
            log.debug("screen %s: %s", h.sid, kind)
            return
        box = self.inbox.setdefault(h.sid, deque(maxlen=INBOX))
        box.append({"t": time.time(), **msg.model_dump(exclude_none=True)})
        log.info("screen %s: %s", h.sid, kind)  # ids and types only, no text

    def broadcast(self, sid: str, msg: BaseModel) -> int:
        """Send any server message (status, plan, voice) to every helper under `sid`."""
        helpers = self.helpers.get(sid, [])
        for h in helpers:
            h.send(msg)
        return len(helpers)

    # ---- server → helper
    def push(self, sid: str, event: OverlayEvent, provider: str = "", model: str = "") -> int:
        """Send one overlay event to every helper under `sid`. Raises ValueError if it breaks a rule."""
        if problem := check_event(event, self.displays(sid)):
            raise ValueError(problem)
        helpers = self.helpers.get(sid, [])
        for h in helpers:
            h.send(OverlayMsg(event=event, provider=provider, model=model))
        return len(helpers)


# ---- rules in code (SCREEN-TUTOR.md, "The guide loop") ------------------------------------------
def check_event(e: OverlayEvent, displays: list[DisplayInfo]) -> str | None:
    """Why this event can't be drawn, or None. Boxes and points are global display points (top-left origin)."""
    needs_box = e.type in ("highlight", "arrow") or (e.type == "pulse" and e.refers_to is None)
    if needs_box and e.bbox is None:
        return f"{e.type} needs a bbox"
    if e.bbox is not None and (e.bbox.w <= 0 or e.bbox.h <= 0):
        return "bbox must have a positive size"
    if e.type == "arrow" and e.from_point is None:
        return "arrow needs from_point"
    if e.type == "caption" and not (e.text or "").strip():
        return "caption needs text"
    if e.type == "phantom_state" and e.state is None:
        return "phantom_state needs state"
    if displays:
        if e.bbox is not None and not any(_contains(d.frame, e.bbox) for d in displays):
            return "bbox is not inside any display"
        if e.from_point is not None and not any(_has_point(d.frame, e.from_point) for d in displays):
            return "from_point is not on any display"
    return None


def _contains(outer: BBox, inner: BBox) -> bool:
    return (
        inner.x >= outer.x - EDGE_PT
        and inner.y >= outer.y - EDGE_PT
        and inner.x + inner.w <= outer.x + outer.w + EDGE_PT
        and inner.y + inner.h <= outer.y + outer.h + EDGE_PT
    )


def _has_point(frame: BBox, p: Point2) -> bool:
    return frame.x <= p.x <= frame.x + frame.w and frame.y <= p.y <= frame.y + frame.h
