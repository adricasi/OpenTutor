"""FastAPI web app: pages, the NDJSON message stream and security middleware (design.md)."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine
from typing import Annotated, Any, cast
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
INTENTS: set[str] = {"ask", "new_exercise", "hint", "answer", "check", "practice", "mark"}
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


class BodyLimit:
    """Cap every request body, with or without Content-Length (security review Blocker).

    Chunked uploads have no Content-Length, so the header check alone let a client stream
    unlimited multipart data into memory or temp files. Counting the bytes FastAPI reads
    keeps every upload under the cap, which is also below the in-memory spool size.
    """

    def __init__(self, app: Any, max_body: int) -> None:
        self.app, self.max_body = app, max_body

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        received = 0
        exceeded = False

        async def limited_receive() -> Any:
            nonlocal received, exceeded
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_body:
                    exceeded = True
                    raise HTTPException(413, "Request too large")  # stops reading the body
            return message

        async def limited_send(message: Any) -> None:
            # FastAPI turns errors while parsing a body into a 400; answer 413 instead.
            if not exceeded:
                await send(message)
            elif message["type"] == "http.response.start":
                body = b'{"detail":"request too large"}'
                headers = [
                    (b"content-type", b"application/json"),
                    (b"content-length", b"%d" % len(body)),
                ]
                await send({"type": "http.response.start", "status": 413, "headers": headers})
                await send({"type": "http.response.body", "body": body})

        await self.app(scope, limited_receive, limited_send)


# Prototype look per subject (docs/prototype, prototype/src/styles.css): colour class, icon, badge.
SUBJECT_LOOKS = {
    "maths": ("maths", "calculator", "Mx"),
    "english-language": ("english", "languages", "En"),
    "science": ("science", "flask-conical", "Sc"),
}


def subject_order(tutor: Tutor) -> tuple[int, str]:
    """Prototype order (Maths, English, Science), then any added tutors by name."""
    order = list(SUBJECT_LOOKS)
    return (order.index(tutor.id) if tutor.id in order else len(order), tutor.config.name)


def subject_look(tutor: Tutor) -> dict[str, str]:
    css, icon, short = SUBJECT_LOOKS.get(tutor.id, ("other", "book-open", tutor.config.name[:2]))
    return {"css": css, "icon": icon, "short": short}


MAX_PAD_LINES = 30
MAX_PAD_LINE_CHARS = 200


def parse_pad_lines(raw: str) -> list[str | None]:
    """The pad's line slots for a live mark: already-read text, or null for a line to read."""
    try:
        slots = json.loads(raw)
    except json.JSONDecodeError as error:
        raise HTTPException(400, "Bad pad lines") from error
    if (
        not isinstance(slots, list)
        or len(slots) > MAX_PAD_LINES
        or not all(
            slot is None or (isinstance(slot, str) and len(slot) <= MAX_PAD_LINE_CHARS)
            for slot in slots
        )
    ):
        raise HTTPException(400, "Bad pad lines")
    return [slot.strip() if isinstance(slot, str) else None for slot in slots]


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

    tutors = dict(
        sorted(load_tutors(settings.tutors_dir).items(), key=lambda kv: subject_order(kv[1]))
    )
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
        checker_fallback=True,  # show the maths checker's verdict even if the model fails
    )
    templates = Jinja2Templates(directory=PACKAGE_DIR / "web" / "templates")
    templates.env.globals["subject_look"] = subject_look
    templates.env.globals["verdict_icons"] = {
        "correct": "circle-check",
        "partly_correct": "circle-alert",
        "not_yet_correct": "circle-x",
    }
    templates.env.globals["verdict_labels"] = {
        "correct": "Correct",
        "partly_correct": "Partly correct",
        "not_yet_correct": "Not yet correct",
    }
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
        context.setdefault("session", None)
        context.setdefault("section", "Join the hub")
        context.setdefault(
            "show_teacher",
            not via_tunnel(request)
            and (settings.teacher_from_network or client_address(request) in LOOPBACK),
        )
        return templates.TemplateResponse(request, name, context, status_code=status)

    @app.get("/", response_class=HTMLResponse)
    async def join_page(request: Request) -> Response:
        if current(request):
            return RedirectResponse("/subjects", status_code=303)
        return page(request, "join.html", bye=request.query_params.get("bye") == "1")

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
        response = RedirectResponse("/?bye=1", status_code=303)
        response.delete_cookie(COOKIE, path="/")
        return response

    @app.get("/subjects", response_class=HTMLResponse)
    async def subjects(request: Request) -> Response:
        session = current(request)
        if not session:
            return RedirectResponse("/", status_code=303)
        return page(
            request,
            "subjects.html",
            section="Your tutors",
            session=session,
            tutors=list(tutors.values()),
        )

    @app.get("/ipad", response_class=HTMLResponse)
    async def ipad(request: Request) -> Response:
        """Send the student to the iPad tutor (REQ-021); it has its own names, so no session."""
        if settings.ipad_url:
            return RedirectResponse(settings.ipad_url, status_code=303)
        return page(request, "ipad.html", section="iPad tutor", session=current(request))

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
        return page(
            request,
            "chat.html",
            section=f"{tutor.config.name} tutor",
            session=session,
            tutor=tutor,
            convo=session.conversation,
            autofill_example=request.query_params.get("example") == "1",
        )

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
        practice: Annotated[str, Form()] = "",
        handwriting: Annotated[str, Form()] = "",
        lines: Annotated[str, Form()] = "",
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
        known_lines = parse_pad_lines(lines) if intent == "mark" and lines else None
        if (
            known_lines
            and sum(len(line or "") for line in known_lines) > settings.max_message_chars
        ):
            raise HTTPException(400, "Bad pad lines")  # the checker gets no more than a message
        photo: bytes | None = None
        if image is not None and image.filename:
            raw = await image.read(settings.max_upload_bytes + 1)
            try:
                async with decode_slots:  # decode off the event loop, two at a time
                    photo = await asyncio.to_thread(
                        prepare_image, raw, settings.max_upload_bytes, settings.image_max_side
                    )
            except ImageRejectedError as error:
                raise HTTPException(400, str(error)) from error
        if not text and photo is None and intent in {"ask", "new_exercise", "check"}:
            raise HTTPException(400, "Type a message or add a photo")
        now = time.monotonic()
        busy_limit = settings.queue_timeout_s + settings.call_timeout_s + 30
        if session.in_flight_since is not None and now - session.in_flight_since < busy_limit:
            raise HTTPException(409, "Please wait for the current answer")
        session.in_flight_since = now  # set before streaming, so a second request gets 409
        tutor = tutors[session.conversation.tutor_id]
        stream = run_request(
            session,
            tutor,
            cast(Intent, intent),
            text,
            photo,
            now,
            practice == "1",
            handwriting == "1",
            known_lines,
        )
        return StreamingResponse(stream, media_type="application/x-ndjson")

    background: set[asyncio.Task[None]] = set()
    decode_slots = asyncio.Semaphore(2)

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

    def state_line(convo: Conversation) -> bytes:
        """The real tutoring state, so the page's hint ladder and exercise panel match it."""
        exercise = convo.exercise.text if convo.exercise else None
        return _line(
            {
                "type": "state",
                "hint_level": convo.hint_level,
                "exercise": exercise,
                "practice": len(convo.practice),
            }
        )

    async def run_request(
        session: Session,
        tutor: Tutor,
        intent: Intent,
        text: str,
        photo: bytes | None,
        accepted_at: float,
        practice_answers: bool,
        handwriting: bool,
        known_lines: list[str | None] | None = None,
    ) -> AsyncIterator[bytes]:
        convo = session.conversation
        if convo is None:
            return
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
                events = tutoring.handle(
                    tutor, convo, intent, text, photo, practice_answers, handwriting, known_lines
                )
                async for event in events:
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
                yield state_line(convo)
                yield _line({"type": "done"})
                record["outcome"] = "ok"
            except Exception as error:  # every failure must end the stream cleanly
                # ModelError/TimeoutError are expected; anything else is logged as a bug but the
                # student still gets a retryable message instead of a broken stream (AC-003.3).
                expected = isinstance(error, ModelError | TimeoutError)
                record["outcome"] = "model_error" if expected else "server_error"
                record["error"] = type(error).__name__
                if not expected:
                    log.exception("unexpected error while answering")
                yield state_line(convo)
                yield _line(
                    _error("model_timeout", "Something went wrong, please try again.", True)
                )
            finally:
                queue.release()
        finally:
            if session.in_flight_since == accepted_at:  # don't clear a newer request's flag
                session.in_flight_since = None
            record["total_s"] = round(time.monotonic() - start, 2)
            log.info(json.dumps(record))

    def teacher_allowed(request: Request) -> None:
        if not settings.teacher_from_network and client_address(request) not in LOOPBACK:
            raise HTTPException(404)

    @app.get("/teacher", response_class=HTMLResponse)
    async def teacher_page(request: Request) -> Response:
        teacher_allowed(request)
        authorised = teacher.is_teacher(request.cookies.get(TEACHER_COOKIE))
        stats = list(insights.by_tutor.values())
        totals = {
            "questions": sum(sum(s.topics.values()) for s in stats),
            "hints": sum(s.hints for s in stats),
            "checked": sum(sum(s.verdicts.values()) for s in stats),
        }
        busiest = max(
            tutors.values(),
            key=lambda t: (
                sum(insights.by_tutor[t.id].topics.values()) if t.id in insights.by_tutor else -1
            ),
        )
        current = tutors.get(request.query_params.get("subject", ""), busiest)
        return page(
            request,
            "teacher.html",
            section="Teacher",
            authorised=authorised,
            insights=insights,
            totals=totals,
            current=current,
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
            return page(
                request, "teacher.html", 401, section="Teacher", authorised=False, error=message
            )
        response = RedirectResponse("/teacher", status_code=303)
        response.set_cookie(TEACHER_COOKIE, teacher.token, httponly=True, samesite="lax")
        return response

    @app.get("/healthz")
    async def healthz(request: Request) -> Response:
        if client_address(request) not in LOOPBACK:
            raise HTTPException(404)
        ok, message = await model.health()
        return JSONResponse({"ok": ok, "model": message}, status_code=200 if ok else 503)

    app.add_middleware(BodyLimit, max_body=max_body)
    if settings.online_demo:
        app.add_middleware(TunnelGate, tunnel_port=settings.tunnel_port, token=app.state.demo_token)
    return app


def _line(event: dict[str, Any]) -> bytes:
    return (json.dumps(event) + "\n").encode()


def _error(code: str, message: str, retryable: bool) -> dict[str, Any]:
    return {"type": "error", "code": code, "message": message, "retryable": retryable}
