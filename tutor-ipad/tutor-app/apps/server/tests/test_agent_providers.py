"""Phase 5 acceptance: the same fixture produces structurally valid feedback from all three providers, the
badge names the one that answered, and a failing cloud provider falls back to the local tutor."""

from __future__ import annotations

import json
import re

import anthropic
import httpx
import httpx2
import openai
import pytest
from fastapi.testclient import TestClient
from inktutor_protocol import FeedbackEvent
from inktutor_server.agent import agent as agent_mod
from inktutor_server.app import create_app
from inktutor_server.config import Settings
from inktutor_server.modes import load_mode
from inktutor_server.tutor.cloud import AnthropicTutor, OpenAITutor
from inktutor_server.tutor.local import LocalMLX
from test_agent import READ_INT1, READ_INT2, FakeRuntime, drive, feedback  # noqa: F401
from test_providers import anthropic_stream, sse


def answer_for(body: str) -> list[str]:
    """What a provider 'says': speak + explain about the first changed line it was shown."""
    lid = re.search(r'\\"line_id\\": \\"(L_[^\\"]+)\\"|"line_id": "(L_[^"]+)"', body)
    line = next(g for g in lid.groups() if g) if lid else "L_unknown"
    return [
        json.dumps({"type": "speak", "line_id": line, "text": "check the power rule"}) + "\n",
        json.dumps({"type": "explain", "line_id": line, "text": "the exponent goes up by one"}) + "\n",
    ]


def make_tutors(monkeypatch, anthropic_fails=False):
    def a_handler(req: httpx2.Request) -> httpx2.Response:
        if anthropic_fails:
            return httpx2.Response(529, json={"type": "error", "error": {"type": "overloaded_error", "message": "x"}})
        return httpx2.Response(
            200,
            content=anthropic_stream(answer_for(req.content.decode())),
            headers={"content-type": "text/event-stream"},
        )

    def o_handler(req: httpx.Request) -> httpx.Response:
        ev = [
            (
                "response.output_text.delta",
                {
                    "type": "response.output_text.delta",
                    "item_id": "m",
                    "output_index": 0,
                    "content_index": 0,
                    "delta": c,
                    "sequence_number": i,
                    "logprobs": [],
                },
            )
            for i, c in enumerate(answer_for(req.content.decode()))
        ]
        return httpx.Response(200, content=sse(ev), headers={"content-type": "text/event-stream"})

    def l_handler(req: httpx.Request) -> httpx.Response:
        chunks = "".join(
            f"data: {json.dumps({'choices': [{'delta': {'content': c}}]})}\n\n"
            for c in answer_for(req.content.decode())
        )
        return httpx.Response(200, content=(chunks + "data: [DONE]\n\n").encode())

    a = anthropic.AsyncAnthropic(
        api_key="t", max_retries=0, http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(a_handler))
    )
    o = openai.AsyncOpenAI(api_key="t", http_client=httpx.AsyncClient(transport=httpx.MockTransport(o_handler)))
    return {
        "local": LocalMLX("http://127.0.0.1:8080/v1", "/served", "gemma4-26b-a4b", httpx.MockTransport(l_handler)),
        "anthropic": AnthropicTutor("t", "claude-sonnet-5-5", a),
        "openai": OpenAITutor("t", "gpt-5", o),
    }


class MultiRuntime(FakeRuntime):
    def __init__(self, tutors):
        self.tutors = tutors

    def tutor(self, provider):
        return self.tutors.get(provider)


@pytest.fixture
def app_with(tmp_path, monkeypatch):
    from inktutor_protocol import ReadingLine

    async def fake_read(self, region, strokes):
        top = region.bbox.y < 170
        done = len(region.stroke_ids) >= (60 if top else 36)
        latex = (READ_INT1 if top else READ_INT2) if done else r"\int_0^1 x^3 dx ="
        return ReadingLine(
            id=region.id, stroke_ids=region.stroke_ids, bbox=region.bbox, kind="math", latex=latex, confidence=0.8
        )

    monkeypatch.setattr(agent_mod.Agent, "_read", fake_read)
    fast = load_mode("math").__class__(**{**load_mode("math").__dict__, "settle_ms": 150})
    monkeypatch.setattr(agent_mod, "load_mode", lambda name: fast)

    def build(tutors):
        app = create_app(Settings(data_dir=tmp_path, tier1_enabled=False, tier2_provider="none"))
        client = TestClient(app)
        client.__enter__()
        app.state.svc.runtime = MultiRuntime(tutors)
        return client

    return build


def run_session(client, provider):
    from test_agent import Inbox, list_fixtures, load, replay_timed

    rec = load(next(p for p in list_fixtures() if p.stem == "rec-math-math-22r_69"))
    sess = client.post("/api/sessions", json={"mode": "math", "problem": "integrals", "provider": provider}).json()
    with client.websocket_connect(f"/ws/session/{sess['id']}") as ws:
        inbox = Inbox(ws)
        inbox.get(5)
        for t, msg in replay_timed(rec):
            if t > 90_500:
                break
            ws.send_json(msg.model_dump(mode="json"))
        return inbox.until(lambda m, out: False, 3.0)


@pytest.mark.parametrize(
    "provider,model", [("local", "gemma4-26b-a4b"), ("anthropic", "claude-sonnet-5-5"), ("openai", "gpt-5")]
)
def test_same_fixture_valid_feedback_from_each_provider(app_with, monkeypatch, provider, model):
    client = app_with(make_tutors(monkeypatch))
    msgs = run_session(client, provider)
    tutor_msgs = [m for m in msgs if m["type"] == "feedback" and m["feedback"]["source"] == "tutor"]
    assert tutor_msgs, f"{provider}: no tutor feedback"
    for m in tutor_msgs:
        FeedbackEvent.model_validate(m["feedback"])  # structurally valid
        assert m["feedback"]["anchor"]["line_id"].startswith("L_")  # anchored to a real line
        assert (m["provider"], m["model"]) == (provider, model)  # the badge names who answered
    client.__exit__(None, None, None)


def test_cloud_failure_falls_back_to_local(app_with, monkeypatch):
    client = app_with(make_tutors(monkeypatch, anthropic_fails=True))
    msgs = run_session(client, "anthropic")
    assert any(m["type"] == "status" and "answering locally" in m.get("detail", "") for m in msgs)
    tutor_msgs = [m for m in msgs if m["type"] == "feedback" and m["feedback"]["source"] == "tutor"]
    assert tutor_msgs and all(m["provider"] == "local" for m in tutor_msgs)
    client.__exit__(None, None, None)
