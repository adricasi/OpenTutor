"""FastAPI web app: pages, the NDJSON message stream and security middleware (design.md)."""

from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Annotated, Any
from urllib.parse import urlsplit

from fastapi import FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.formparsers import MultiPartParser

from .config import PACKAGE_DIR, Settings
from .images import ImageRejectedError, prepare_image
from .model import FakeModel, ModelClient, ModelError, OllamaModel
from .queue import FairQueue, QueueTimeoutError
from .sessions import Conversation, JoinError, Session, SessionStore
from .tutoring import Intent, Limits, Tutoring
from .tutors import Tutor, load_tutors

log = logging.getLogger("opentutor")
COOKIE = "ot_session"
INTENTS: set[str] = {"ask", "new_exercise", "hint", "answer", "check", "practice"}
SECURITY_HEADERS = {
    "content-security-policy": (
        "default-src 'self'; img-src 'self' blob: data:; script-src 'self'; style-src 'self'; "
        "form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
    ),
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
    "cache-control": "no-store",
}


def session_ref(session_id: str) -> str:
    """Short, non-reversible reference for logs: never log the cookie value itself."""
    return hashlib.sha256(session_id.encode()).hexdigest()[:12]


def client_address(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def create_app(settings: Settings | None = None, model: ModelClient | None = None) -> FastAPI:
    settings = settings or Settings()
    if model is None:
        model = (
            FakeModel()
            if settings.model_backend == "fake"
            else OllamaModel(settings.ollama_url, settings.model, settings.model_digest)
        )
    # Keep photo uploads in memory: Starlette spools file parts > 1 MB to a temp file on disk
    # by default, which would break AC-004.6 / NFR-002.
    MultiPartParser.spool_max_size = settings.max_upload_bytes + 1024 * 1024
    max_body = settings.max_upload_bytes + 1024 * 1024

    tutors = load_tutors(settings.tutors_dir)
    store = SessionStore(
        settings.max_sessions,
        settings.max_sessions_per_address,
        settings.join_attempts_per_minute,
        settings.session_idle_minutes * 60,
    )
    queue = FairQueue(settings.parallel_requests, settings.queue_timeout_s, settings.queue_update_s)
    tutoring = Tutoring(
        model,
        settings.prompts_dir,
        Limits(
            first_output_s=settings.first_output_timeout_s,
            call_s=settings.call_timeout_s,
            turns_kept=settings.turns_kept,
        ),
    )
    templates = Jinja2Templates(directory=PACKAGE_DIR / "web" / "templates")

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings, app.state.store, app.state.queue = settings, store, queue
    app.state.model, app.state.tutors = model, tutors
    app.mount("/static", StaticFiles(directory=PACKAGE_DIR / "web" / "static"), name="static")

    @app.middleware("http")
    async def guard(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.method in {"POST", "PUT", "DELETE"}:
            origin = request.headers.get("origin")
            if origin and urlsplit(origin).netloc != request.headers.get("host"):
                return JSONResponse({"detail": "cross-origin request refused"}, status_code=403)
            length = request.headers.get("content-length")
            if length and length.isdigit() and int(length) > max_body:
                return JSONResponse({"detail": "request too large"}, status_code=413)
        response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        return response

    def current(request: Request) -> Session | None:
        return store.get(request.cookies.get(COOKIE))

    def page(request: Request, name: str, status: int = 200, **context: Any) -> HTMLResponse:
        return templates.TemplateResponse(request, name, context, status_code=status)

    @app.get("/", response_class=HTMLResponse)
    async def join_page(request: Request) -> Response:
        if current(request):
            return RedirectResponse("/subjects", status_code=303)
        return page(request, "join.html")

    @app.post("/session")
    async def join(request: Request, nickname: Annotated[str, Form()] = "") -> Response:
        try:
            session = store.join(nickname, client_address(request))
        except JoinError as error:
            return page(request, "join.html", error.status, error=error.message, nickname=nickname)
        response = RedirectResponse("/subjects", status_code=303)
        response.set_cookie(COOKIE, session.id, httponly=True, samesite="lax", path="/")
        return response

    @app.post("/session/end")
    async def finish(request: Request) -> Response:
        session_id = request.cookies.get(COOKIE)
        if session_id:
            store.end(session_id)
        response = RedirectResponse("/", status_code=303)
        response.delete_cookie(COOKIE, path="/")
        return response

    @app.get("/subjects", response_class=HTMLResponse)
    async def subjects(request: Request) -> Response:
        session = current(request)
        if not session:
            return RedirectResponse("/", status_code=303)
        return page(request, "subjects.html", session=session, tutors=list(tutors.values()))

    def tutor_or_404(tutor_id: str) -> Tutor:
        tutor = tutors.get(tutor_id)
        if tutor is None:
            raise HTTPException(404, "No such subject tutor")
        return tutor

    @app.get("/chat/{tutor_id}", response_class=HTMLResponse)
    async def chat(request: Request, tutor_id: str) -> Response:
        session = current(request)
        if not session:
            return RedirectResponse("/", status_code=303)
        tutor = tutor_or_404(tutor_id)
        if session.conversation is None or session.conversation.tutor_id != tutor.id:
            session.conversation = Conversation(tutor.id)  # switching subject starts afresh
        return page(request, "chat.html", session=session, tutor=tutor, convo=session.conversation)

    @app.post("/chat/{tutor_id}/new")
    async def new_chat(request: Request, tutor_id: str) -> Response:
        session = current(request)
        if not session:
            return RedirectResponse("/", status_code=303)
        session.conversation = Conversation(tutor_or_404(tutor_id).id)
        return RedirectResponse(f"/chat/{tutor_id}", status_code=303)

    @app.post("/api/messages")
    async def messages(
        request: Request,
        intent: Annotated[str, Form()] = "ask",
        text: Annotated[str, Form()] = "",
        image: UploadFile | None = None,
    ) -> Response:
        session = current(request)
        if not session or session.conversation is None:
            raise HTTPException(401, "Join the hub and choose a subject first")
        if intent not in INTENTS:
            raise HTTPException(400, "Unknown action")
        text = text.strip()
        if len(text) > settings.max_message_chars:
            raise HTTPException(
                400, f"Messages can be at most {settings.max_message_chars} characters"
            )
        photo: bytes | None = None
        if image is not None and image.filename:
            try:
                photo = prepare_image(
                    await image.read(settings.max_upload_bytes + 1),
                    settings.max_upload_bytes,
                    settings.image_max_side,
                )
            except ImageRejectedError as error:
                raise HTTPException(400, str(error)) from error
        if not text and photo is None and intent in {"ask", "new_exercise", "check"}:
            raise HTTPException(400, "Type a message or add a photo")
        if session.in_flight:
            raise HTTPException(409, "Please wait for the current answer")
        tutor = tutors[session.conversation.tutor_id]
        stream = run_request(session, tutor, intent, text, photo)  # type: ignore[arg-type]
        return StreamingResponse(stream, media_type="application/x-ndjson")

    async def run_request(
        session: Session, tutor: Tutor, intent: Intent, text: str, photo: bytes | None
    ) -> AsyncIterator[bytes]:
        convo = session.conversation
        if convo is None:
            return
        session.in_flight = True
        record: dict[str, Any] = {
            "request": uuid.uuid4().hex[:12],
            "session": session_ref(session.id),
            "tutor": tutor.id,
            "intent": intent,
            "photo": photo is not None,
        }
        start = time.monotonic()
        try:
            try:
                async for position in queue.wait_turn():
                    yield _line({"type": "queued", "position": position})
            except QueueTimeoutError:
                record["outcome"] = "queue_timeout"
                yield _line(_error("queue_timeout", "The tutor is busy. Please try again.", True))
                return
            record["queue_s"] = round(time.monotonic() - start, 2)
            try:
                yield _line({"type": "started"})
                async for event in tutoring.handle(tutor, convo, intent, text, photo):
                    if "first_token_s" not in record and event["type"] == "token":
                        record["first_token_s"] = round(time.monotonic() - start, 2)
                    yield _line(event)
                yield _line({"type": "done"})
                record["outcome"] = "ok"
            except (ModelError, TimeoutError) as error:
                record["outcome"], record["error"] = "model_error", type(error).__name__
                yield _line(
                    _error("model_timeout", "Something went wrong, please try again.", True)
                )
            finally:
                queue.release()
        finally:
            session.in_flight = False
            record["total_s"] = round(time.monotonic() - start, 2)
            log.info(json.dumps(record))

    @app.get("/healthz")
    async def healthz(request: Request) -> Response:
        if client_address(request) not in {"127.0.0.1", "::1"}:
            raise HTTPException(404)
        ok, message = await model.health()
        return JSONResponse({"ok": ok, "model": message}, status_code=200 if ok else 503)

    return app


def _line(event: dict[str, Any]) -> bytes:
    return (json.dumps(event) + "\n").encode()


def _error(code: str, message: str, retryable: bool) -> dict[str, Any]:
    return {"type": "error", "code": code, "message": message, "retryable": retryable}
