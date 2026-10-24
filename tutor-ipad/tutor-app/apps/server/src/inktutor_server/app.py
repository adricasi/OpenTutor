"""FastAPI app: REST (sessions, pages, settings) + `ws/session/{id}` (ink in, readings/feedback out)."""

from __future__ import annotations

import asyncio
import logging
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from inktutor_protocol import (
    ClientMessage,
    ErrorMsg,
    Mode,
    PageRef,
    Provider,
    RuntimeMsg,
    RuntimeStatus,
    Session,
    SessionMsg,
    TutorInfo,
)
from pydantic import BaseModel, TypeAdapter, ValidationError

from .agent import Agent
from .config import APP_ROOT, Settings, configure_hf
from .demo import install as install_demo
from .pairing import install as install_pairing
from .store import Store

log = logging.getLogger("inktutor")
CLIENT_MSG: TypeAdapter[Any] = TypeAdapter(ClientMessage)


class NewSession(BaseModel):
    mode: Mode = "math"
    problem: str = ""
    provider: Provider | None = None


class PhotoUpload(BaseModel):
    data_url: str  # data:image/jpeg;base64,… (the client downsizes to ≤ 1600 px)


class SettingsUpdate(BaseModel):
    local_model: str | None = None


class Services:
    """Process-wide services shared by sessions (models are loaded once)."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.store = Store(settings.data_dir / "inktutor.sqlite3")
        self.agents: dict[str, Agent] = {}
        self.runtime: Any = None  # set in lifespan when tier-1/tier-2 are enabled

    def agent(self, session: Session) -> Agent:
        if session.id not in self.agents:
            self.agents[session.id] = Agent(session, self)
        return self.agents[session.id]

    def tutor_info(self, provider: str) -> TutorInfo:
        if self.runtime is None:
            return TutorInfo(provider="local", model="none", ready=False, detail="tier 2 disabled")
        return self.runtime.tutor_info(provider)

    def runtime_status(self) -> RuntimeStatus:
        """Memory watchdog UI: free memory + how the local tier-2 model runs (pushed as `runtime` messages)."""
        status = getattr(self.runtime, "status", None)
        if callable(status):
            return status()  # type: ignore[no-any-return]
        from .runtime import memory

        s = self.settings
        avail = memory.available_gb()
        return RuntimeStatus(
            available_gb=round(avail, 1),
            level=memory.level(avail, s.memory_low_gb, s.memory_critical_gb),
            tier2_model=s.local_model,
            tier2_mode="off",
            detail="tier 2 disabled",
        )


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    configure_hf(settings)
    if not logging.getLogger("inktutor").handlers:
        h = logging.StreamHandler()
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s", "%H:%M:%S"))
        logging.getLogger("inktutor").addHandler(h)
        logging.getLogger("inktutor").setLevel(logging.INFO)

    @asynccontextmanager
    async def lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
        svc = Services(settings)
        app.state.svc = svc
        if settings.tier1_enabled or settings.tier2_provider != "none":
            from .runtime import Runtime

            svc.runtime = Runtime(settings)
            await svc.runtime.start()
        try:
            yield
        finally:
            for a in svc.agents.values():
                a.close()
            if svc.runtime:
                await svc.runtime.stop()
            svc.store.close()

    app = FastAPI(title="Ink Tutor", lifespan=lifespan)

    def svc() -> Services:
        return app.state.svc  # type: ignore[no-any-return]

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"ok": "true"}

    @app.get("/api/status")
    def get_status() -> RuntimeStatus:
        return svc().runtime_status()

    @app.get("/api/settings")
    def get_settings() -> dict[str, Any]:
        rt = svc().runtime
        if rt is None:
            return {
                "provider": "none",
                "local_model": settings.local_model,
                "local_models": [],
                "loading": None,
                "cloud": {"anthropic": False, "openai": False},
            }
        return rt.settings_view(settings.tier2_provider)  # type: ignore[no-any-return]

    @app.put("/api/settings", status_code=202)
    async def put_settings(body: SettingsUpdate) -> dict[str, Any]:
        rt = svc().runtime
        if rt is None:
            raise HTTPException(409, "models are disabled on this server")
        if body.local_model:
            if f"tier2.{body.local_model}" not in rt.manifest:
                raise HTTPException(404, f"unknown model {body.local_model}")
            if rt.loading:
                raise HTTPException(409, f"already loading {rt.loading}")
            rt._spawn(rt.use_local_model(body.local_model))  # swap in the background; UI polls /api/settings
        return rt.settings_view(settings.tier2_provider)  # type: ignore[no-any-return]

    @app.post("/api/sessions")
    def new_session(body: NewSession) -> Session:
        provider = body.provider or (settings.tier2_provider if settings.tier2_provider != "none" else "local")
        s = Session(id="sess_" + uuid.uuid4().hex[:12], mode=body.mode, problem=body.problem, provider=provider)  # type: ignore[arg-type]
        svc().store.put_session(s)
        return s

    @app.get("/api/sessions/{sid}")
    def get_session(sid: str) -> Session:
        s = svc().store.get_session(sid)
        if not s:
            raise HTTPException(404, "no such session")
        return s

    @app.post("/api/sessions/{sid}/photos")
    async def add_photo(sid: str, body: PhotoUpload) -> dict[str, Any]:
        """A photo of paper → a new page; reading and marks stream over the session's WebSocket."""
        import base64
        import io

        from PIL import Image

        session = svc().store.get_session(sid)
        if not session:
            raise HTTPException(404, "no such session")
        if svc().runtime is None:
            raise HTTPException(409, "models are disabled on this server")
        try:
            raw = base64.b64decode(body.data_url.split(",", 1)[1])
            image = Image.open(io.BytesIO(raw))
            image.load()
        except Exception as e:
            raise HTTPException(400, f"not an image: {e}") from e
        pid = "ph_" + uuid.uuid4().hex[:10]
        photos = settings.data_dir / "photos"
        photos.mkdir(parents=True, exist_ok=True)
        image.convert("RGB").save(photos / f"{pid}.jpg", quality=88)
        page = PageRef(id=pid, index=len(session.pages), width=image.width, height=image.height)
        session.pages.append(page)
        svc().store.put_session(session)
        svc().store.put_page(sid, page)
        agent = svc().agent(session)
        agent.session.pages = session.pages
        svc().runtime._spawn(agent.on_photo(page, image))
        return {"page": page.model_dump(), "url": f"/api/photos/{pid}"}

    @app.get("/api/photos/{pid}")
    def get_photo(pid: str) -> FileResponse:
        f = (settings.data_dir / "photos" / f"{pid}.jpg").resolve()
        if not f.is_relative_to((settings.data_dir / "photos").resolve()) or not f.exists():
            raise HTTPException(404, "no such photo")
        return FileResponse(f, media_type="image/jpeg")

    @app.get("/api/sessions/{sid}/pages/{page_id}/strokes")
    def page_strokes(sid: str, page_id: str) -> JSONResponse:
        return JSONResponse([s.model_dump(mode="json") for s in svc().store.strokes(sid, page_id)])

    @app.websocket("/ws/session/{sid}")
    async def ws_session(ws: WebSocket, sid: str) -> None:
        await ws.accept()
        session = svc().store.get_session(sid)
        if not session:
            await ws.send_json(ErrorMsg(message="no such session").model_dump(mode="json"))
            await ws.close()
            return
        agent = svc().agent(session)
        out: asyncio.Queue[Any] = asyncio.Queue()
        agent.subscribe(out)

        async def pump() -> None:
            while True:
                msg = await out.get()
                await ws.send_json(msg.model_dump(mode="json", exclude_none=True))

        sender = asyncio.create_task(pump())
        await out.put(SessionMsg(session=session, tier2=svc().tutor_info(session.provider)))

        async def runtime_ticker() -> None:  # memory watchdog UI: on connect, then every few seconds
            while True:
                await out.put(RuntimeMsg(status=svc().runtime_status()))
                await asyncio.sleep(settings.runtime_status_interval_s)

        ticker = asyncio.create_task(runtime_ticker())
        try:
            while True:
                raw = await ws.receive_json()
                try:
                    msg = CLIENT_MSG.validate_python(raw)
                except ValidationError as e:
                    await out.put(ErrorMsg(message=f"bad message: {e.errors()[0]['msg']}"))
                    continue
                await agent.handle(msg)
        except WebSocketDisconnect:
            pass
        finally:
            agent.unsubscribe(out)
            # drain what is already queued so acks reach the client before the socket goes away
            sender.cancel()
            ticker.cancel()

    # iPad pairing (tunnel token gate + GET /api/pairing) and the scripted demo (GET /api/demo/{stem})
    install_pairing(app, settings)
    install_demo(app, settings)

    # the built web app (apps/web/dist), if present
    dist = APP_ROOT / "apps" / "web" / "dist"
    if dist.exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str) -> FileResponse:
            f = dist / path
            return FileResponse(
                f if path and f.is_file() and Path(f).resolve().is_relative_to(dist) else dist / "index.html"
            )

    return app
