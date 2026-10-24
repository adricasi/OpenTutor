"""Phase 1 acceptance: a replay test streams a fixture's strokes into the WebSocket and the store holds them."""

import pytest
from fastapi.testclient import TestClient
from inktutor_server.app import create_app
from inktutor_server.config import Settings
from inktutor_server.fixtures import list_fixtures, load, replay

STROKE_FIXTURES = [p for p in list_fixtures() if load(p).stats.strokes > 0]


@pytest.fixture
def client(tmp_path):
    app = create_app(Settings(data_dir=tmp_path, tier1_enabled=False, tier2_provider="none"))
    with TestClient(app) as c:
        yield c


def test_there_are_stroke_fixtures():
    assert STROKE_FIXTURES, "no recordings with strokes in fixtures/recordings"


@pytest.mark.parametrize("path", STROKE_FIXTURES, ids=lambda p: p.stem)
def test_replay_streams_fixture_into_store(client, path):
    rec = load(path)
    session = client.post("/api/sessions", json={"mode": rec.mode, "problem": rec.session.problem}).json()
    sent = 0
    with client.websocket_connect(f"/ws/session/{session['id']}") as ws:
        ready = ws.receive_json()
        assert ready["type"] == "session"
        for msg in replay(rec, speed=0):  # speed=0: no sleeping, same batching as a real client
            ws.send_json(msg.model_dump(mode="json"))
            sent += 1
            while True:  # every ink.update is acknowledged with a status for its page
                ev = ws.receive_json()
                if ev["type"] == "status" and ev["phase"] == "received":
                    break
    assert sent > 1, "replay should batch strokes into several ink.update messages (pen-up debounce)"

    page = rec.pages[0]
    stored = client.get(f"/api/sessions/{session['id']}/pages/{page.id}/strokes").json()
    want = {s.id: len(s.pts) for s in page.strokes}
    got = {s["id"]: len(s["pts"]) for s in stored}
    assert got == want  # same final stroke set (erases applied), every point kept
    first = next(s for s in stored if s["id"] == page.strokes[0].id)
    assert first["pts"][0] == list(page.strokes[0].pts[0])  # values untouched (ink stays ink)
