"""FastAPI web app: pages, the NDJSON message stream and security middleware (design.md)."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine
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
from .insights import Insights, TeacherGate
from .model import FakeModel, ModelClient, ModelError, OllamaModel
from .queue import FairQueue, QueueCancelledError, QueueTimeoutError
from .sessions import Conversation, Exercise, JoinError, Session, SessionStore
from .tunnel import TunnelGate, is_tunnel, new_token
from .tutoring import Intent, Limits, Tutoring
from .tutors import Tutor, load_tutors

log = logging.getLogger("opentutor")
COOKIE = "ot_session"
TEACHER_COOKIE = "ot_teacher"
LOOPBACK = {"127.0.0.1", "::1"}
INTENTS: set[str] = {"ask", "new_exercise", "hint", "answer", "check", "practice"}
SECURITY_HEADERS = {
    "content-security-policy": (
        "default-src 'self'; img-src 'self' blob: data:; script-src 'self'; style-src 'self'; "
        "form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
    ),
    "x-content-type-options": "nosniff",
    # same-origin, not no-referrer: with no-referrer Chrome sends "Origin: null" on form posts,
    # which the Origin check below would refuse.
    "referrer-policy": "same-origin",
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
            else OllamaModel(
                settings.ollama_url,
                settings.model,
                settings.model_digest,
                settings.model_keep_alive,
            )
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
    insights, teacher = Insights(), TeacherGate()

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings, app.state.store, app.state.queue = settings, store, queue
    app.state.model, app.state.tutors = model, tutors
    app.state.insights, app.state.teacher = insights, teacher
    app.state.demo_token = new_token() if settings.online_demo else None
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

    def via_tunnel(request: Request) -> bool:
        return settings.online_demo and is_tunnel(request.scope, settings.tunnel_port)

    def current(request: Request) -> Session | None:
        return store.get(request.cookies.get(COOKIE))

    def page(request: Request, name: str, status: int = 200, **context: Any) -> HTMLResponse:
        context.setdefault("online_demo", via_tunnel(request))
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
        response.set_cookie(
            COOKIE, session.id, httponly=True, samesite="lax", path="/", secure=via_tunnel(request)
        )
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

    background: set[asyncio.Task[None]] = set()

    def track(coroutine: Coroutine[Any, Any, None]) -> None:
        task = asyncio.create_task(coroutine)
        background.add(task)  # keep a reference until it finishes
        task.add_done_callback(background.discard)

    async def classify_later(tutor: Tutor, exercise: Exercise) -> None:
        """Topic for a typed exercise, after the student's reply, behind waiting students."""
        topic = "other"
        try:
            async for _ in queue.wait_turn():
                pass
            try:
                topic = await tutoring.classify_topic(tutor, exercise)
                exercise.topic = topic
            finally:
                queue.release()
        except (ModelError, TimeoutError, QueueTimeoutError) as error:
            log.info(json.dumps({"background": "classify_topic", "error": type(error).__name__}))
        insights.exercise(tutor.id, topic)

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
        exercise_before = convo.exercise
        try:
            try:
                async for position in queue.wait_turn(lambda: session.ended):
                    yield _line({"type": "queued", "position": position})
            except QueueCancelledError:
                record["outcome"] = "session_ended"
                return
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
                    if event["type"] == "verdict" and convo.exercise:
                        insights.verdict(tutor.id, convo.exercise.topic, event["verdict"])
                    yield _line(event)
                new_exercise = convo.exercise
                if new_exercise is not None and new_exercise is not exercise_before:
                    if new_exercise.image is None:
                        track(classify_later(tutor, new_exercise))
                    else:
                        insights.exercise(tutor.id, new_exercise.topic)
                if intent == "hint":
                    insights.hint(tutor.id)
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

    def teacher_allowed(request: Request) -> None:
        if not settings.teacher_from_network and client_address(request) not in LOOPBACK:
            raise HTTPException(404)

    @app.get("/teacher", response_class=HTMLResponse)
    async def teacher_page(request: Request) -> Response:
        teacher_allowed(request)
        authorised = teacher.is_teacher(request.cookies.get(TEACHER_COOKIE))
        return page(
            request,
            "teacher.html",
            authorised=authorised,
            insights=insights,
            tutors=list(tutors.values()),
        )

    @app.post("/teacher/login")
    async def teacher_login(request: Request, pin: Annotated[str, Form()] = "") -> Response:
        teacher_allowed(request)
        address = client_address(request)
        if not teacher.login(address, pin):
            message = (
                "Too many wrong tries. Wait 5 minutes."
                if teacher.locked(address)
                else "Wrong PIN. Check the hub laptop's screen."
            )
            return page(request, "teacher.html", 401, authorised=False, error=message)
        response = RedirectResponse("/teacher", status_code=303)
        response.set_cookie(TEACHER_COOKIE, teacher.token, httponly=True, samesite="lax")
        return response

    @app.get("/healthz")
    async def healthz(request: Request) -> Response:
        if client_address(request) not in LOOPBACK:
            raise HTTPException(404)
        ok, message = await model.health()
        return JSONResponse({"ok": ok, "model": message}, status_code=200 if ok else 503)

    if settings.online_demo:
        app.add_middleware(TunnelGate, tunnel_port=settings.tunnel_port, token=app.state.demo_token)
    return app


def _line(event: dict[str, Any]) -> bytes:
    return (json.dumps(event) + "\n").encode()


def _error(code: str, message: str, retryable: bool) -> dict[str, Any]:
    return {"type": "error", "code": code, "message": message, "retryable": retryable}
