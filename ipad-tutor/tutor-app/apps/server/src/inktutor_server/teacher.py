"""Teacher view: homework links, a progress table per homework, read-only sessions, CSV.

`/teacher` and `/api/teacher/*` are for the Mac (localhost / LAN). Through the Cloudflare tunnel they are
refused, even with the pairing cookie every student's iPad has, unless a separate `TEACHER_TOKEN` is set: then
`?t=<token>` once sets an HttpOnly cookie. Students only ever see `GET /api/homework/{id}` (title and problem,
never who else did it). Everything stays on the Mac; summaries are local or a template (progress.py).
"""

from __future__ import annotations

import csv
import hmac
import io
import re
import time
import unicodedata
import uuid
from datetime import UTC, datetime
from http.cookies import SimpleCookie
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qsl, urlencode

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response
from inktutor_protocol import (
    Homework,
    HomeworkCreate,
    HomeworkProgress,
    HomeworkPublic,
    LearningLog,
    ProgressSummary,
    Session,
    SessionBrief,
    StudentRow,
)
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from .config import APP_ROOT, Settings
from .pairing import _respond, is_tunnel_request, lan_url, qr_svg_data_uri, read_state, read_token, with_token
from .progress import Summarizer, compute_log

if TYPE_CHECKING:
    from .app import Services
    from .store import Store

COOKIE = "inktutor_t"
PREFIXES = ("/teacher", "/api/teacher")
REFUSED_HTML = (
    b"<!doctype html><meta name=viewport content='width=device-width'><body style='font:17px system-ui;"
    b"padding:24px'><h3>The teacher view is on the Mac</h3><p>Open <code>http://localhost:8000/teacher</code> on "
    b"the Mac (or its LAN address). Through the tunnel it needs the teacher link (<code>TEACHER_TOKEN</code>).</p>"
)


class TeacherSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=APP_ROOT / ".env", extra="ignore", populate_by_name=True)
    teacher_token: str = Field("", alias="TEACHER_TOKEN")


def is_teacher_path(path: str) -> bool:
    return any(path == p or path.startswith(p + "/") or path.startswith(p + "?") for p in PREFIXES)


class TeacherGate:
    """ASGI middleware: teacher paths through the tunnel need TEACHER_TOKEN (query once, then cookie); without
    one they are refused there. LAN and localhost stay open (like the app itself)."""

    def __init__(self, app: Any, token: str) -> None:
        self.app = app
        self.token = token

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] not in ("http", "websocket") or not is_teacher_path(scope["path"]):
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        if not is_tunnel_request(headers):
            await self.app(scope, receive, send)
            return
        if self.token:
            jar = SimpleCookie(headers.get("cookie", ""))
            got = jar[COOKIE].value if COOKIE in jar else ""
            if got and hmac.compare_digest(got, self.token):
                await self.app(scope, receive, send)
                return
            query = parse_qsl(scope.get("query_string", b"").decode("latin-1"), keep_blank_values=True)
            t = next((v for name, v in query if name == "t"), "")
            if scope["type"] == "http" and t and hmac.compare_digest(t, self.token):
                rest = urlencode([(n, v) for n, v in query if n != "t"])
                location = scope["path"] + (f"?{rest}" if rest else "")
                cookie = f"{COOKIE}={self.token}; Path=/; Max-Age=43200; HttpOnly; SameSite=Strict; Secure"
                return await _respond(
                    send, 302, b"", "text/plain", [(b"location", location.encode()), (b"set-cookie", cookie.encode())]
                )
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return None
        if scope["path"].startswith("/api/"):
            return await _respond(send, 403, b'{"detail":"teacher view: Mac only"}', "application/json")
        return await _respond(send, 403, REFUSED_HTML, "text/html; charset=utf-8")


# ---------------------------------------------------------------- students and homework sessions
_CTRL = re.compile(r"[\x00-\x1f\x7f<>]")


def clean_name(name: str | None) -> str | None:
    """A first name as typed on a device: trimmed, one line, ≤ 40 characters; None if empty."""
    if not name:
        return None
    s = unicodedata.normalize("NFC", _CTRL.sub("", name))
    s = " ".join(s.split())[:40]
    return s or None


def homework_session(store: Store, s: Session, student: str | None, homework_id: str | None) -> Session:
    """POST /api/sessions from a homework link: the homework's mode and problem (the server's copy), the name."""
    hw = store.get_homework(homework_id) if homework_id else None
    update: dict[str, Any] = {"student": clean_name(student)}
    if hw is not None:
        update |= {"homework_id": hw.id, "mode": hw.mode, "problem": hw.problem}
    out = s.model_copy(update=update)
    store.put_session(out)
    return out


def _key(name: str | None) -> str:
    return (name or "").strip().casefold()


# ---------------------------------------------------------------- tables
def student_link(settings: Settings, hid: str) -> str:
    """The tunnel link when `make pair` runs (the iPads' usual way in), else the LAN one."""
    state, token = read_state(settings.data_dir), read_token(settings.data_dir)
    if state.get("url") and token:
        return with_token(f"{state['url']}/?hw={hid}", token)
    return f"{lan_url(settings.port)}?hw={hid}"


def _status(lg: LearningLog) -> str:
    if lg.finished:
        return "done"
    return "in_progress" if lg.strokes or lg.lines_written else "not_started"


def homework_rows(svc: Services, hw: Homework) -> list[StudentRow]:
    summ = Summarizer.of(svc)
    groups: dict[str, list[tuple[Session, LearningLog]]] = {}
    for s, _created in svc.store.sessions(hw.id):
        groups.setdefault(_key(s.student), []).append((s, compute_log(svc.store, s, hw)))
    rows: list[StudentRow] = []
    order = {"done": 0, "in_progress": 1, "not_started": 2}
    for items in groups.values():
        logs = [lg for _, lg in items]
        best = min(
            items, key=lambda it: (order[_status(it[1])], -(it[1].last_activity or it[1].created))
        )  # finished first, else the latest with ink
        status = min((_status(lg) for lg in logs), key=order.__getitem__)
        rows.append(
            StudentRow(
                student=best[0].student or "(no name)",
                status=status,  # type: ignore[arg-type]
                session_id=best[0].id,
                sessions=[s.id for s, _ in items],
                time_on_task_s=round(sum(lg.time_on_task_s for lg in logs), 1),
                hints=sum(sum(lg.hints) for lg in logs),
                hints_explicit=sum(lg.hints[3] for lg in logs),
                mistakes=sum(lg.mistakes for lg in logs),
                mistakes_fixed=sum(lg.mistakes_fixed for lg in logs),
                last_activity=max(lg.last_activity or lg.created for lg in logs),
                summary=summ.cached(best[0], best[1]),
            )
        )
    rows.sort(key=lambda r: (order[r.status], r.student.casefold()))
    return rows


def loose_sessions(svc: Services) -> list[SessionBrief]:
    """Sessions without (a known) homework that have ink, newest first."""
    known = {h.id for h in svc.store.list_homework()}
    summ = Summarizer.of(svc)
    out: list[SessionBrief] = []
    for s, created in reversed(svc.store.sessions()):
        if s.homework_id in known:
            continue
        lg = compute_log(svc.store, s)
        if not (lg.strokes or lg.lines_written):
            continue
        brief = SessionBrief(
            session_id=s.id, mode=s.mode, problem=s.problem, student=s.student, created=created, log=lg
        )
        brief.summary = summ.cached(s, lg)
        out.append(brief)
    return out


_CSV_COLUMNS = [
    "homework",
    "student",
    "status",
    "minutes",
    "lines_written",
    "lines_checked",
    "mistakes",
    "mistakes_fixed",
    "hints_1_nudge",
    "hints_2_hint",
    "hints_3_step",
    "hints_4_solution",
    "questions_answered",
    "corrections",
    "summary",
    "summary_source",
    "last_activity",
    "session_id",
]


def _cell(v: object) -> str:
    """No spreadsheet formulas from what a child typed (CSV injection): =, +, -, @ get a leading quote."""
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, UTC).isoformat(timespec="seconds") if t else ""


def to_csv(rows: list[tuple[str, Session, LearningLog, ProgressSummary]]) -> str:
    """One row per session: (homework title, session, log, summary)."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(_CSV_COLUMNS)
    for title, s, lg, summary in rows:
        w.writerow(
            [
                _cell(x)
                for x in (
                    title,
                    s.student or "",
                    _status(lg),
                    round(lg.time_on_task_s / 60, 1),
                    lg.lines_written,
                    lg.lines_checked,
                    lg.mistakes,
                    lg.mistakes_fixed,
                    *lg.hints,
                    lg.questions_answered,
                    lg.corrections,
                    summary.text,
                    summary.source,
                    _iso(lg.last_activity or lg.created),
                    s.id,
                )
            ]
        )
    return buf.getvalue()


# ---------------------------------------------------------------- routes
def install(app: FastAPI, settings: Settings, teacher_token: str | None = None) -> None:
    """Teacher routes + the tunnel gate for them. Call before the SPA catch-all route is added."""
    token = TeacherSettings().teacher_token if teacher_token is None else teacher_token  # type: ignore[call-arg]
    app.add_middleware(TeacherGate, token=token)

    def svc(request: Request) -> Services:
        return request.app.state.svc  # type: ignore[no-any-return]

    def get_hw(request: Request, hid: str) -> Homework:
        hw = svc(request).store.get_homework(hid)
        if hw is None:
            raise HTTPException(404, "no such homework")
        return hw

    @app.get("/api/homework/{hid}")
    def homework_public(request: Request, hid: str) -> HomeworkPublic:
        """For a student's homework link: only what the start screen shows."""
        hw = get_hw(request, hid)
        return HomeworkPublic(id=hw.id, title=hw.title, mode=hw.mode, problem=hw.problem, due=hw.due)

    @app.get("/api/teacher/homework")
    def list_homework(request: Request) -> list[dict[str, Any]]:
        out = []
        for hw in svc(request).store.list_homework():
            rows = homework_rows(svc(request), hw)
            out.append(
                {
                    "homework": hw.model_dump(mode="json"),
                    "students": len(rows),
                    "done": sum(r.status == "done" for r in rows),
                }
            )
        return out

    @app.post("/api/teacher/homework")
    def create_homework(request: Request, body: HomeworkCreate) -> Homework:
        hw = Homework(id="hw_" + uuid.uuid4().hex[:8], created=time.time(), **_hw_fields(body))
        svc(request).store.put_homework(hw)
        return hw

    @app.put("/api/teacher/homework/{hid}")
    def update_homework(request: Request, hid: str, body: HomeworkCreate) -> Homework:
        old = get_hw(request, hid)
        hw = Homework(id=old.id, created=old.created, **_hw_fields(body))
        svc(request).store.put_homework(hw)
        return hw

    @app.delete("/api/teacher/homework/{hid}", status_code=204)
    def delete_homework(request: Request, hid: str) -> Response:
        if not svc(request).store.delete_homework(hid):
            raise HTTPException(404, "no such homework")
        return Response(status_code=204)  # its sessions stay, listed as sessions without homework

    @app.get("/api/teacher/homework/{hid}")
    def homework_progress(request: Request, hid: str) -> HomeworkProgress:
        hw = get_hw(request, hid)
        link = student_link(settings, hw.id)
        return HomeworkProgress(homework=hw, link=link, qr=qr_svg_data_uri(link), rows=homework_rows(svc(request), hw))

    @app.get("/api/teacher/sessions")
    def sessions_without_homework(request: Request) -> list[SessionBrief]:
        return loose_sessions(svc(request))

    @app.get("/api/teacher/sessions/{sid}")
    def session_detail(request: Request, sid: str) -> dict[str, Any]:
        """A session, read-only: its strokes (erased ones flagged), every feedback event, the latest readings."""
        store = svc(request).store
        s = store.get_session(sid)
        if s is None:
            raise HTTPException(404, "no such session")
        hw = store.get_homework(s.homework_id) if s.homework_id else None
        lg = compute_log(store, s, hw)
        readings = {r.page_id: r.model_dump(mode="json", exclude_none=True) for r in store.readings(sid)}
        feedback = [ev for ev, _ in store.feedback_log(sid)]
        page_ids = [p.id for p in s.pages] or sorted({ev.page_id for ev in feedback} | set(readings))
        pages: list[dict[str, Any]] = []
        for pid in page_ids:
            ref = next((p for p in s.pages if p.id == pid), None)
            strokes = store.all_strokes(sid, pid)
            pages.append(
                {
                    "page": ref.model_dump(mode="json")
                    if ref
                    else {"id": pid, "index": len(pages), "width": 1194, "height": 628},
                    "strokes": [st.model_dump(mode="json") for st, _ in strokes],
                    "erased": [st.id for st, gone in strokes if gone],
                    "reading": readings.get(pid),
                    "feedback": [ev.model_dump(mode="json", exclude_none=True) for ev in feedback if ev.page_id == pid],
                }
            )
        return {
            "session": s.model_dump(mode="json"),
            "homework": hw.model_dump(mode="json") if hw else None,
            "log": lg.model_dump(mode="json"),
            "summary": Summarizer.of(svc(request)).cached(s, lg).model_dump(mode="json"),
            "pages": pages,
        }

    @app.post("/api/teacher/sessions/{sid}/summary")
    async def refresh_summary(request: Request, sid: str, cloud: bool = False) -> dict[str, Any]:
        """Refresh: written now by the local tutor if it is loaded and no student's review is running (a cloud
        tutor only with `cloud=true` on a cloud session); else the template now and the tutor's version later."""
        summ = Summarizer.of(svc(request))
        s = svc(request).store.get_session(sid)
        if s is None:
            raise HTTPException(404, "no such session")
        busy = summ.busy()
        out = await summ.refresh(sid, cloud=cloud)
        queued = busy and out.source == "template" and summ.tutor_for(s, cloud) is not None
        if queued:
            summ.schedule(sid)  # written by the tutor once the students' reviews are done
        return {"summary": out.model_dump(mode="json"), "queued": queued}

    @app.get("/api/teacher/export.csv")
    def export_csv(request: Request, homework: str = "") -> Response:
        """`?homework=<id>` (one homework), `?homework=none` (sessions without homework), or everything."""
        store = svc(request).store
        summ = Summarizer.of(svc(request))
        hws = {h.id: h for h in store.list_homework()}
        if homework and homework != "none" and homework not in hws:
            raise HTTPException(404, "no such homework")
        rows = []
        for s, _created in store.sessions():
            hw = hws.get(s.homework_id or "")
            if homework == "none" and hw is not None:
                continue
            if homework not in ("", "none") and (hw is None or hw.id != homework):
                continue
            lg = compute_log(store, s, hw)
            if hw is None and not (lg.strokes or lg.lines_written):
                continue
            rows.append((hw.title if hw else "", s, lg, summ.cached(s, lg)))
        name = f"ink-tutor-{homework or 'all'}-{datetime.now(UTC):%Y%m%d}.csv"
        return Response(
            to_csv(rows),
            media_type="text/csv; charset=utf-8",
            headers={"content-disposition": f'attachment; filename="{name}"', "cache-control": "no-store"},
        )


def _hw_fields(body: HomeworkCreate) -> dict[str, Any]:
    d = body.model_dump()
    d["title"] = " ".join(body.title.split())
    d["answer"] = (body.answer or "").strip() or None
    d["due"] = (body.due or "").strip() or None
    if d["due"] is not None and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d["due"]):
        raise HTTPException(422, "due must be YYYY-MM-DD")
    return d
