"""`/ws/screen/{sid}` for the overlay helper, plus the localhost-only dev routes that push overlay events to it.

Screen data never goes through the Cloudflare tunnel, and nothing but a local process may
drive the overlay, so every route here refuses:
- requests that came through the tunnel (`pairing.is_tunnel_request`; a paired iPad's cookie doesn't help);
- clients that aren't on the loopback interface (the server listens on 0.0.0.0 for the iPad on the LAN);
- a Host that isn't a loopback name, and any `Origin` header: browsers always send one, the helper and curl
  don't, so a web page (or a DNS-rebinding trick) can't reach these routes from the Mac's own browser.

    curl -X POST http://127.0.0.1:8000/api/screen/mac/overlay -H 'content-type: application/json' \\
      -d '{"id":"h1","type":"highlight","bbox":{"x":100,"y":80,"w":60,"h":28},"ttl_ms":8000}'

The guide loop (`ScreenSession` + `guide.loop.Guide`, Phases 12–13) is the normal source of overlay events; the
push route stays for checking the overlay by hand. `GET /api/screen/{id}` also shows the guide's state (phase,
step, plan, the app and how many AX elements and frames the server has).
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from inktutor_protocol import ErrorMsg, OverlayEvent, RuntimeMsg, ScreenClientMessage, ScreenConfigMsg
from pydantic import TypeAdapter, ValidationError

from ..config import Settings
from ..guide.config import load_config
from ..pairing import is_tunnel_request
from .hub import ScreenHub

if TYPE_CHECKING:
    from .session import ScreenSession

log = logging.getLogger("inktutor.screen")

SID_RE = re.compile(r"[A-Za-z0-9_.-]{1,64}")
LOOPBACK_NAMES = {"localhost", "127.0.0.1", "::1"}
CLIENT_MSG: TypeAdapter[Any] = TypeAdapter(ScreenClientMessage)


def _is_loopback(host: str | None) -> bool:
    if not host:
        return False
    if host == "localhost":
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    mapped = ip.ipv4_mapped if isinstance(ip, ipaddress.IPv6Address) else None
    return (mapped or ip).is_loopback


def _host_name(host_header: str) -> str:
    h = host_header.strip().lower()
    if h.startswith("["):  # [::1]:8000
        return h[1:].split("]", 1)[0]
    return h.split(":", 1)[0] if h.count(":") <= 1 else h


def refuse_reason(client_host: str | None, headers: Mapping[str, str]) -> str | None:
    """Why this request may not use the screen routes, or None if it comes from a local process."""
    hdrs = {k.lower(): v for k, v in headers.items()}
    if is_tunnel_request(hdrs):
        return "the screen tutor is not available through the tunnel"
    if not _is_loopback(client_host):
        return "the screen tutor is localhost only"
    if _host_name(hdrs.get("host", "")) not in LOOPBACK_NAMES:
        return "the screen tutor answers only to a loopback Host"
    if "origin" in hdrs:
        return "browsers can't use the screen tutor routes"
    return None


def blocklist(settings: Settings) -> list[str]:
    return [a.strip() for a in settings.screen_blocklist.split(",") if a.strip()]


def install(app: FastAPI, settings: Settings) -> ScreenHub:
    """The helper's WebSocket and the dev routes. Call before the SPA catch-all route is added."""
    hub = ScreenHub()
    hub.cfg = load_config()
    app.state.screen_hub = hub

    def make_session(sid: str) -> ScreenSession:
        from .session import ScreenSession  # here: session → guide.loop → screen.commands would be circular

        svc = getattr(app.state, "svc", None)  # set in the app's lifespan; svc.runtime may be None (no models)
        return ScreenSession(sid, hub, svc, hub.cfg, blocklist(settings))

    hub.make_session = make_session

    def gate(request: Request, sid: str | None = None) -> None:
        if reason := refuse_reason(request.client.host if request.client else None, request.headers):
            raise HTTPException(403, reason)
        if sid is not None and not SID_RE.fullmatch(sid):
            raise HTTPException(422, "bad screen session id")

    @app.websocket("/ws/screen/{sid}")
    async def ws_screen(ws: WebSocket, sid: str) -> None:
        reason = refuse_reason(ws.client.host if ws.client else None, ws.headers)
        if reason or not SID_RE.fullmatch(sid):
            log.warning("screen %s: refused (%s)", sid[:64], reason or "bad id")
            await ws.close(code=1008)  # before accept → HTTP 403
            return
        await ws.accept()
        helper = hub.connect(sid)
        session = hub.session(sid)
        helper.send(
            ScreenConfigMsg(
                blocklist=blocklist(settings),
                capture_fps=hub.cfg.capture_fps,
                allow_automation=settings.allow_automation,
            )
        )

        async def pump() -> None:
            while True:
                msg = await helper.out.get()
                await ws.send_text(msg.model_dump_json())

        async def runtime_ticker() -> None:  # the phantom menu's memory light, like the canvas toolbar's
            svc = getattr(app.state, "svc", None)
            while svc is not None:
                await asyncio.sleep(settings.runtime_status_interval_s)
                helper.send(RuntimeMsg(status=svc.runtime_status()))

        sender = asyncio.create_task(pump())
        ticker = asyncio.create_task(runtime_ticker())
        try:
            while True:
                raw = await ws.receive_text()
                try:
                    msg = CLIENT_MSG.validate_json(raw)
                except ValidationError as e:
                    helper.send(ErrorMsg(message=f"bad message: {e.errors()[0]['msg']}"))
                    continue
                hub.receive(helper, msg)
                if session is not None:
                    try:
                        await session.handle(msg)
                    except Exception:  # one bad message must not drop the helper
                        log.exception("screen %s: %s failed", sid, getattr(msg, "type", "?"))
                        helper.send(ErrorMsg(message=f"server error on {getattr(msg, 'type', 'message')}"))
        except WebSocketDisconnect:
            pass
        finally:
            sender.cancel()
            ticker.cancel()
            hub.disconnect(helper)

    @app.get("/api/screen")
    def screen_sessions(request: Request) -> dict[str, Any]:
        gate(request)
        return {"helpers": {sid: len(hs) for sid, hs in hub.helpers.items()}}

    @app.get("/api/screen/{sid}")
    def screen_session(sid: str, request: Request) -> dict[str, Any]:
        gate(request, sid)
        hellos = [h.hello for h in hub.helpers.get(sid, []) if h.hello is not None]
        return {
            "connected": hub.connected(sid),
            "helper": hellos[-1].helper if hellos else None,
            "displays": [d.model_dump() for d in hub.displays(sid)],
            "received": list(hub.inbox.get(sid, [])),
            "guide": s.describe() if (s := hub.sessions.get(sid)) is not None else None,
        }

    @app.post("/api/screen/{sid}/overlay", status_code=202)
    async def push_overlay(sid: str, event: OverlayEvent, request: Request) -> dict[str, Any]:
        """Dev/test: draw one overlay event on the helper(s) connected under `sid`."""
        gate(request, sid)
        if not hub.connected(sid):
            raise HTTPException(404, f"no overlay helper connected as {sid!r} (run `make overlay`)")
        try:
            delivered = hub.push(sid, event)
        except ValueError as e:
            raise HTTPException(422, str(e)) from None
        return {"delivered": delivered, "id": event.id}

    return hub
