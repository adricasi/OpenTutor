"""Teacher view: the learning log, summaries, homework, the tunnel gate, the CSV.

The log is computed on the real maths recording replayed through the real agent (fake reader + fake tutor,
the harness of test_agent.py), and on hand-made store contents for the rules the recording doesn't exercise.
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import time
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from inktutor_protocol import Anchor, FeedbackEvent, Homework, LearningLog, LogLine, ReadingLine, Session, Stroke
from inktutor_server import pairing, progress
from inktutor_server.agent import agent as agent_mod
from inktutor_server.app import Services, create_app
from inktutor_server.config import Settings
from inktutor_server.progress import Summarizer, compute_log, log_hash, template_summary
from inktutor_server.tutor.local import LocalMLX
from test_agent import FakeRuntime, FakeTutor, drive, feedback, harness  # noqa: F401  (harness is a fixture)

TUNNEL = {"host": "abc-def.trycloudflare.com", "cf-ray": "8f00-MAD", "cf-connecting-ip": "203.0.113.7"}


# ---------------------------------------------------------------- the log, on the replayed maths recording
def test_learning_log_from_the_replayed_maths_recording(harness, monkeypatch):  # noqa: F811
    client, _, _ = harness

    def work(ws, inbox, page_id, got):
        cross = next(f for f in feedback(got) if f["type"] == "mark" and f["glyph"] == "cross")
        out = []
        for level in (1, 3):  # a nudge, then the corrected step (a ghost)
            ws.send_json(
                {
                    "type": "hint.request",
                    "page_id": page_id,
                    "feedback_id": cross["id"],
                    "line_id": cross["anchor"]["line_id"],
                    "level": level,
                    "explicit": False,
                }
            )
            out += inbox.until(lambda m, _: m["type"] == "status" and m["phase"] in ("waiting", "error"), 5)
        # the student then writes the corrected step themselves, further down: the ghost retires
        s = Stroke(id="s_fix", page_id=page_id, pts=[(100000, 120, 520, 0.2, 0, 0), (100010, 160, 522, 0.2, 0, 0)])
        ws.send_json({"type": "ink.update", "page_id": page_id, "strokes_changed": [s.model_dump(mode="json")]})
        return out + inbox.until(lambda m, out: False, 1.5)

    async def read_fix(self, region, strokes):
        if "s_fix" in region.stroke_ids:
            return ReadingLine(
                id=region.id,
                stroke_ids=region.stroke_ids,
                bbox=region.bbox,
                kind="math",
                latex=r"\left[\frac{x^{4}}{4}\right]_{0}^{1}=\frac{1}{4}",
                confidence=0.9,
            )
        return await original(self, region, strokes)

    original = agent_mod.Agent._read
    monkeypatch.setattr(agent_mod.Agent, "_read", read_fix)
    fb = feedback(drive(client, wait_s=1.5, then=work))
    ghost = next(f for f in fb if f["type"] == "ghost")
    assert any(f["type"] == "retract" and f["refers_to"] == ghost["id"] for f in fb)

    store = client.app.state.svc.store
    [(session, _)] = store.sessions()
    lg = compute_log(store, session)
    assert lg.lines_checked >= 3 and lg.lines_written >= lg.lines_checked
    eq = next(v for v in lg.verdicts if v.kind == "math.equivalent")  # the kind comes from the checker's verdict
    assert eq.ok >= 2 and eq.wrong >= 1  # ∫₂⁴ right, ∫₀¹ wrong, the fix right
    assert lg.mistakes == 1 and lg.mistakes_fixed == 1 and lg.ghosts_retired == 1
    wrong = next(ln for ln in lg.lines if ln.was_wrong)
    assert wrong.fixed and wrong.hint_level == 3 and "x" in wrong.reads
    assert lg.hints[0] == 1 and lg.hints[2] == 1 and lg.hints[3] == 0
    assert lg.finished is True and lg.finished_by == "last_line"  # the last checked line is ✓
    assert 60 < lg.time_on_task_s < 200 and lg.strokes > 50  # first to last ink, erased strokes included
    assert compute_log(store, session) == lg and log_hash(compute_log(store, session)) == log_hash(lg)

    # a homework answer decides "finished" instead of the last line
    hw = Homework(id="hw_x", title="Integrals", problem="", answer="1/4", created=time.time())
    assert compute_log(store, session, hw).finished_by == "answer"
    assert compute_log(store, session, hw.model_copy(update={"answer": "3/4"})).finished is False


# ---------------------------------------------------------------- the log, on hand-made store contents
def _ev(sid: str, store: Any, **kw: Any) -> FeedbackEvent:
    ev = FeedbackEvent(page_id="p1", **kw)
    store.put_feedback(sid, ev)
    time.sleep(0.002)  # distinct timestamps (the log is ordered by them)
    return ev


def _verdict(store: Any, sid: str, lid: str, ok: bool, bbox: list[float], kind: str = "arithmetic.long_division"):
    store.log_activity(sid, "p1", "verdict", lid, {"ok": ok, "kind": kind, "reads": "84:4", "bbox": bbox})
    time.sleep(0.002)


@pytest.fixture
def svc(tmp_path):
    s = Services(Settings(data_dir=tmp_path, tier1_enabled=False, tier2_provider="none"))
    yield s
    s.store.close()


def test_mistake_rewritten_on_the_same_spot_counts_as_fixed(svc):
    store = svc.store
    session = Session(id="s1", mode="math", problem="84 ÷ 4", student="Marta")
    store.put_session(session)
    store.apply_ink(
        "s1", "p1", [Stroke(id="a", page_id="p1", pts=[(1000, 0, 0, 0.3, 0, 0), (61000, 1, 1, 0.3, 0, 0)])], []
    )
    _ev("s1", store, id="f1", anchor=Anchor(line_id="L_a"), type="mark", glyph="cross", source="checker")
    _verdict(store, "s1", "L_a", False, [40, 100, 200, 40])
    _ev("s1", store, id="f2", anchor=Anchor(line_id="L_a"), type="hint", hint_level=1, text="look at the tens")
    _ev("s1", store, id="f3", anchor=Anchor(line_id="L_a"), type="hint", hint_level=4, text="the solution")
    _ev("s1", store, id="f4", anchor=Anchor(line_id="L_a"), type="retract", refers_to="f1", source="recognizer")
    # erased and rewritten in the same place: a new line id, the same region → the mistake was fixed
    _ev("s1", store, id="f5", anchor=Anchor(line_id="L_b"), type="mark", glyph="check", source="checker")
    _verdict(store, "s1", "L_b", True, [42, 104, 190, 38])
    _ev(
        "s1",
        store,
        id="f6",
        anchor=Anchor(line_id="L_c"),
        type="question",
        text="I read this as 21?",
        source="recognizer",
    )
    store.log_activity("s1", "p1", "answer", "L_c", {"feedback_id": "f6", "choice": "yes"})
    store.log_activity("s1", "p1", "correction", "L_c", {"source": "typed"})
    # a ✗ on another row that stays wrong
    _ev("s1", store, id="f7", anchor=Anchor(line_id="L_d"), type="mark", glyph="cross", source="checker")
    _verdict(store, "s1", "L_d", False, [40, 300, 200, 40])

    lg = compute_log(store, session)
    assert lg.mistakes == 2 and lg.mistakes_fixed == 1
    assert {ln.line_id: ln.fixed for ln in lg.lines if ln.was_wrong} == {"L_a": True, "L_d": False}
    assert lg.hints == [1, 0, 0, 1]
    assert lg.questions_asked == 1 and lg.questions_answered == 1 and lg.corrections == 1
    assert lg.time_on_task_s == 60.0 and lg.finished is False  # the last checked line is ✗
    assert [(v.kind, v.ok, v.wrong) for v in lg.verdicts] == [("arithmetic.long_division", 1, 2)]

    text = template_summary(lg)
    assert text.startswith("Marta practised maths (“84 ÷ 4”) for about 1 minute")
    assert "fixed 1 themselves; still to work on: long division" in text
    assert "including the full solution" in text and "haven't finished" in text


def test_template_summary_is_2_to_5_plain_sentences():
    lg = LearningLog(
        session_id="s",
        mode="math",
        problem="Compute ∫₀¹ x³ dx",
        student="Pau",
        lines_written=3,
        lines_checked=2,
        mistakes=1,
        mistakes_fixed=1,
        hints=[1, 0, 1, 0],
        strokes=80,
        time_on_task_s=420,
        finished=True,
        finished_by="last_line",
        lines=[
            LogLine(line_id="a", page_id="p", kind="math.equivalent", ok=True),
            LogLine(line_id="b", page_id="p", kind="math.equivalent", ok=True, was_wrong=True, fixed=True),
        ],
    )
    text = template_summary(lg)
    assert text == (
        "Pau practised maths (“Compute ∫₀¹ x³ dx”) for about 7 minutes, writing 3 lines. "
        "1 checked line of 2 was right first time (step-by-step working). "
        "They made 1 mistake and fixed it themselves. They asked for 2 hints. They finished the work."
    )
    for t in (text, template_summary(LearningLog(session_id="s", mode="consulting", student="Pau", strokes=12))):
        n = len([s for s in t.replace("…", "").split(". ") if s])
        assert 2 <= n <= 5 and "\\" not in t and "$" not in t
    empty = template_summary(LearningLog(session_id="s", mode="sudoku"))
    assert empty == "The student opened sudoku but hasn't written anything yet. There is nothing else to report."


# ---------------------------------------------------------------- summarize, through the local tutor's HTTP path
def _sse(text: str) -> bytes:
    chunks = [text[i : i + 17] for i in range(0, len(text), 17)]
    body = "".join(f"data: {json.dumps({'choices': [{'delta': {'content': c}}]})}\n\n" for c in chunks)
    return (body + "data: [DONE]\n\n").encode()


class SummaryServer:
    """A fake mlx-vlm endpoint: records requests, streams one scripted reply."""

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.requests: list[dict[str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(json.loads(request.content))
        return httpx.Response(200, content=_sse(self.reply), headers={"content-type": "text/event-stream"})


def _rig(svc: Services, reply: str) -> SummaryServer:
    server = SummaryServer(reply)
    tutor = LocalMLX("http://mlx.test/v1", "served", "gemma4-26b-a4b", transport=httpx.MockTransport(server))
    svc.runtime = FakeRuntime(tutor)
    s = Session(id="s1", mode="math", problem="Compute ∫₀¹ x³ dx", student="Pau")
    svc.store.put_session(s)
    svc.store.apply_ink(
        "s1", "p1", [Stroke(id="a", page_id="p1", pts=[(0, 0, 0, 0.3, 0, 0), (90000, 1, 1, 0.3, 0, 0)])], []
    )
    _ev("s1", svc.store, id="f1", anchor=Anchor(line_id="L_a"), type="mark", glyph="check", source="checker")
    _verdict(svc.store, "s1", "L_a", True, [0, 0, 10, 10], kind="math.equivalent")
    return server


GOOD = (
    '{"type":"summary","text":"Pau practised definite integrals. They worked out the antiderivative of x cubed '
    'correctly. Nothing was marked wrong. They needed no hints."}\n'
)


async def test_summarize_through_the_local_tutor_then_cached(svc):
    server = _rig(svc, GOOD)
    s = await Summarizer.of(svc).refresh("s1")
    assert s.source == "local · gemma4-26b-a4b" and s.text.startswith("Pau practised definite integrals.")
    [req] = server.requests
    system, user = req["messages"][0]["content"], req["messages"][1]["content"]
    assert "progress note for a teacher" in system and "No LaTeX" in system
    assert all(p["type"] == "text" for p in user)  # the log only: no images, no strokes
    sent = user[0]["text"]
    assert '"student": "Pau"' in sent and "math.equivalent" in sent and '"pts"' not in sent
    assert svc.store.get_summary("s1") == s
    assert await Summarizer.of(svc).refresh("s1") == s and len(server.requests) == 1  # same log: not rewritten
    _ev("s1", svc.store, id="f2", anchor=Anchor(line_id="L_a"), type="hint", hint_level=1, text="nudge")
    assert (await Summarizer.of(svc).refresh("s1")).log_hash != s.log_hash and len(server.requests) == 2


@pytest.mark.parametrize(
    "reply",
    [
        '{"type":"summary","text":"They wrote \\\\frac{x^4}{4}."}\n',
        "no json at all\n",
        '{"type":"speak","text":"hi"}\n',
    ],
    ids=["latex", "not-json", "wrong-type"],
)
async def test_unusable_tutor_output_falls_back_to_the_template(svc, reply):
    _rig(svc, reply)
    s = await Summarizer.of(svc).refresh("s1")
    assert s.source == "template" and s.text.startswith("Pau practised maths")


async def test_no_tutor_loaded_or_a_review_in_flight_means_the_template(svc):
    server = _rig(svc, GOOD)
    svc.runtime.loading = "qwen36-35b-a3b"  # tier 2 is (re)loading: never wait for or load a model for this
    assert (await Summarizer.of(svc).refresh("s1")).source == "template"
    svc.runtime.loading = None
    agent = svc.agent(svc.store.get_session("s1"))
    agent.reviewing.add("p1")  # a student's review is streaming: don't compete for the GPU
    assert (await Summarizer.of(svc).refresh("s1")).source == "template"
    assert server.requests == []
    agent.reviewing.clear()
    assert (await Summarizer.of(svc).refresh("s1")).source.startswith("local")  # the template is upgraded


async def test_cloud_only_when_the_session_is_cloud_and_asked(svc):
    _rig(svc, GOOD)

    class Cloud(FakeTutor):
        name, model, label = "anthropic", "claude-x", "claude-x"
        calls = 0

        async def summarize(self, ctx):
            Cloud.calls += 1
            yield {"type": "summary", "text": "From the cloud. Second sentence."}

    local = svc.runtime.tutor("local")
    svc.runtime.tutor = lambda p: Cloud([]) if p == "anthropic" else local
    summ = Summarizer.of(svc)
    assert (await summ.refresh("s1", cloud=True)).source.startswith("local")  # a local session: stays local
    s = svc.store.get_session("s1")
    svc.store.put_session(s.model_copy(update={"provider": "anthropic"}))
    assert (await summ.refresh("s1")).source.startswith("local")  # cloud session, not asked: local
    assert (await summ.refresh("s1", cloud=True)).source == "anthropic · claude-x" and Cloud.calls == 1


async def test_ended_session_is_summarized_and_new_ink_makes_it_yield(svc, monkeypatch):
    _rig(svc, GOOD)
    started, release = asyncio.Event(), asyncio.Event()

    class Slow(FakeTutor):
        async def summarize(self, ctx):
            started.set()
            await release.wait()
            yield {"type": "summary", "text": "Written after the student paused. Second sentence."}

    slow = Slow([])
    svc.runtime = FakeRuntime(slow)
    monkeypatch.setattr(progress, "END_DELAY_S", 0.01)
    monkeypatch.setattr(progress, "BUSY_POLL_S", 0.01)
    agent = svc.agent(svc.store.get_session("s1"))
    q: asyncio.Queue[Any] = asyncio.Queue()
    agent.subscribe(q)
    agent.unsubscribe(q)  # the last socket closed: the session ended
    await asyncio.wait_for(started.wait(), 2)
    progress.on_ink(agent)  # new ink somewhere: the summary yields the GPU (and is retried)
    started.clear()
    await asyncio.wait_for(started.wait(), 2)  # …written again once nobody is busy
    release.set()
    for _ in range(100):
        s = svc.store.get_summary("s1")
        if s is not None:
            break
        await asyncio.sleep(0.01)
    assert s is not None and s.text.startswith("Written after the student paused")
    Summarizer.of(svc).close()


# ---------------------------------------------------------------- homework CRUD, sessions, gate, CSV
@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("TEACHER_TOKEN", "")  # an empty env var also overrides a token in tutor-app/.env
    app = create_app(Settings(data_dir=tmp_path, tier1_enabled=False, tier2_provider="none"))
    with TestClient(app) as c:
        yield c


def test_homework_crud_and_the_session_fields(client):
    hw = client.post(
        "/api/teacher/homework",
        json={"title": "  Definite  integrals ", "mode": "math", "problem": "∫₀¹ x³ dx", "due": "2026-10-30"},
    ).json()
    assert hw["id"].startswith("hw_") and hw["title"] == "Definite integrals" and hw["answer"] is None
    assert client.post("/api/teacher/homework", json={"title": "x", "due": "next week"}).status_code == 422
    assert client.post("/api/teacher/homework", json={"title": ""}).status_code == 422
    [item] = client.get("/api/teacher/homework").json()
    assert item["homework"]["id"] == hw["id"] and item["students"] == 0
    upd = client.put(
        f"/api/teacher/homework/{hw['id']}", json={"title": "Integrals", "problem": "∫₀¹ x³ dx", "answer": "1/4"}
    )
    assert upd.json()["answer"] == "1/4" and upd.json()["created"] == hw["created"]

    public = client.get(f"/api/homework/{hw['id']}").json()
    assert public == {"id": hw["id"], "title": "Integrals", "mode": "math", "problem": "∫₀¹ x³ dx", "due": None}
    # a homework link: the name (cleaned) and the homework; mode and problem are the server's copy
    s = client.post(
        "/api/sessions", json={"mode": "sudoku", "problem": "x", "student": " Marta\n <b>", "homework_id": hw["id"]}
    ).json()
    assert (s["student"], s["homework_id"], s["mode"], s["problem"]) == ("Marta b", hw["id"], "math", "∫₀¹ x³ dx")
    assert client.get(f"/api/sessions/{s['id']}").json()["student"] == "Marta b"  # stored
    plain = client.post("/api/sessions", json={"mode": "math", "problem": "free practice"}).json()
    assert plain["student"] is None and plain["homework_id"] is None  # sessions without homework: as before
    assert client.post("/api/sessions", json={"homework_id": "hw_gone"}).json()["homework_id"] is None

    prog = client.get(f"/api/teacher/homework/{hw['id']}").json()
    assert prog["link"].endswith(f"/?hw={hw['id']}") and prog["qr"].startswith("data:image/svg+xml")
    [row] = prog["rows"]
    assert row["student"] == "Marta b" and row["status"] == "not_started" and row["summary"]["source"] == "template"
    assert "hasn't written anything yet" in row["summary"]["text"]

    assert client.delete(f"/api/teacher/homework/{hw['id']}").status_code == 204
    assert client.get(f"/api/homework/{hw['id']}").status_code == 404
    assert client.delete(f"/api/teacher/homework/{hw['id']}").status_code == 404


def test_homework_link_is_the_tunnel_one_when_make_pair_runs(client, tmp_path):
    hw = client.post("/api/teacher/homework", json={"title": "T"}).json()
    token = pairing.load_token(tmp_path)
    pairing.write_state(tmp_path, url="https://lemon-tiger.trycloudflare.com", reachable=True)
    link = client.get(f"/api/teacher/homework/{hw['id']}").json()["link"]
    assert link == f"https://lemon-tiger.trycloudflare.com/?hw={hw['id']}&k={token}"


def test_teacher_routes_are_refused_through_the_tunnel(client, tmp_path):
    hw = client.post("/api/teacher/homework", json={"title": "T", "problem": "p"}).json()  # LAN / localhost: open
    student = {**TUNNEL, "cookie": f"{pairing.COOKIE}={pairing.load_token(tmp_path)}"}  # a paired iPad
    for path in ("/api/teacher/homework", f"/api/teacher/homework/{hw['id']}", "/api/teacher/sessions", "/teacher"):
        assert client.get(path, headers=student).status_code == 403, path
    assert client.get("/api/teacher/export.csv", headers=student).status_code == 403
    assert client.post("/api/teacher/homework", json={"title": "x"}, headers=student).status_code == 403
    assert (
        client.get(
            "/api/teacher/homework", headers={**student, "cookie": student["cookie"] + "; inktutor_t=x"}
        ).status_code
        == 403
    )
    # the student's own homework link works through the tunnel, and tells nothing about other students
    public = client.get(f"/api/homework/{hw['id']}", headers=student)
    assert public.status_code == 200 and set(public.json()) == {"id", "title", "mode", "problem", "due"}


def test_teacher_token_opens_the_tunnel_with_a_cookie(tmp_path, monkeypatch):
    monkeypatch.setenv("TEACHER_TOKEN", "s3cret-teacher")
    app = create_app(Settings(data_dir=tmp_path, tier1_enabled=False, tier2_provider="none"))
    with TestClient(app) as c:
        paired = {**TUNNEL, "cookie": f"{pairing.COOKIE}={pairing.load_token(tmp_path)}"}
        assert c.get("/api/teacher/homework", headers=paired).status_code == 403
        assert c.get("/api/teacher/homework", headers=paired, params={"t": "wrong"}).status_code == 403
        r = c.get("/teacher", headers=paired, params={"t": "s3cret-teacher", "x": "1"}, follow_redirects=False)
        assert r.status_code == 302 and r.headers["location"] == "/teacher?x=1"
        cookie = r.headers["set-cookie"]
        assert "inktutor_t=s3cret-teacher" in cookie and "HttpOnly" in cookie and "Secure" in cookie
        both = {**TUNNEL, "cookie": f"{paired['cookie']}; inktutor_t=s3cret-teacher"}
        assert c.get("/api/teacher/homework", headers=both).status_code == 200
        assert c.get("/api/teacher/homework").status_code == 200  # localhost needs no token


def test_csv_export(client):
    hw = client.post("/api/teacher/homework", json={"title": "Integrals", "problem": "∫"}).json()
    svc = client.app.state.svc
    a = client.post("/api/sessions", json={"student": "=HYPERLINK(1)", "homework_id": hw["id"]}).json()
    client.post("/api/sessions", json={"student": "Pau", "homework_id": hw["id"]})
    loose = client.post("/api/sessions", json={"mode": "math"}).json()
    svc.store.apply_ink(
        a["id"], "p1", [Stroke(id="s", page_id="p1", pts=[(0, 0, 0, 0.3, 0, 0), (120000, 1, 1, 0.3, 0, 0)])], []
    )
    _ev(a["id"], svc.store, id="h1", anchor=Anchor(line_id="L_s"), type="hint", hint_level=2, text="h")
    svc.store.apply_ink(loose["id"], "p1", [Stroke(id="t", page_id="p1", pts=[(0, 0, 0, 0.3, 0, 0)])], [])

    r = client.get("/api/teacher/export.csv", params={"homework": hw["id"]})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    rows = list(csv.DictReader(io.StringIO(r.text)))
    assert [x["student"] for x in rows] == ["'=HYPERLINK(1)", "Pau"]  # no formulas from what a child typed
    first = rows[0]
    assert first["homework"] == "Integrals" and first["status"] == "in_progress" and first["minutes"] == "2.0"
    assert first["hints_2_hint"] == "1" and first["summary_source"] == "template" and first["summary"]
    none = list(csv.DictReader(io.StringIO(client.get("/api/teacher/export.csv", params={"homework": "none"}).text)))
    assert [x["session_id"] for x in none] == [loose["id"]]  # sessions without homework that have ink
    assert len(list(csv.DictReader(io.StringIO(client.get("/api/teacher/export.csv").text)))) == 3
    assert client.get("/api/teacher/export.csv", params={"homework": "hw_nope"}).status_code == 404


def test_session_detail_and_refresh(client):
    svc = client.app.state.svc
    s = client.post("/api/sessions", json={"mode": "math", "problem": "p"}).json()
    svc.store.apply_ink(s["id"], "p1", [Stroke(id="a", page_id="p1", pts=[(0, 1, 1, 0.3, 0, 0)])], ["a"])
    svc.store.apply_ink(s["id"], "p1", [Stroke(id="b", page_id="p1", pts=[(5, 1, 1, 0.3, 0, 0)])], [])
    _ev(s["id"], svc.store, id="m1", anchor=Anchor(line_id="L_b"), type="mark", glyph="check", source="checker")
    d = client.get(f"/api/teacher/sessions/{s['id']}").json()
    [page] = d["pages"]
    assert [x["id"] for x in page["strokes"]] == ["a", "b"] and page["erased"] == ["a"]
    assert [f["id"] for f in page["feedback"]] == ["m1"] and d["log"]["lines_checked"] == 1
    [brief] = client.get("/api/teacher/sessions").json()
    assert brief["session_id"] == s["id"] and brief["summary"]["text"]
    r = client.post(f"/api/teacher/sessions/{s['id']}/summary").json()
    assert r["summary"]["source"] == "template" and r["queued"] is False  # no models on this server
    assert svc.store.get_summary(s["id"]) is not None
    assert client.post("/api/teacher/sessions/nope/summary").status_code == 404
