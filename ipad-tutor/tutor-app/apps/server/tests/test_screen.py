"""Phase 11: the overlay helper's endpoint (/ws/screen/{id}), its local-only gate, and the dev push route."""

import pytest
from fastapi.testclient import TestClient
from inktutor_server import pairing
from inktutor_server.app import create_app
from inktutor_server.config import Settings
from inktutor_server.screen import refuse_reason
from starlette.websockets import WebSocketDisconnect

LOCAL = {"base_url": "http://127.0.0.1:8000", "client": ("127.0.0.1", 51000)}
WS = "ws://127.0.0.1:8000/ws/screen/"  # absolute: TestClient's websocket_connect ignores base_url
TUNNEL = {"host": "abc-def.trycloudflare.com", "cf-ray": "8f00-MAD", "cf-connecting-ip": "203.0.113.7"}
HELLO = {
    "type": "screen.hello",
    "helper": "inktutor-overlay test",
    "displays": [{"id": 1, "frame": {"x": 0, "y": 0, "w": 1512, "h": 982}, "scale": 2, "main": True}],
}
INSERT = {"x": 318, "y": 58, "w": 52, "h": 24}


@pytest.fixture
def settings(tmp_path):
    return Settings(data_dir=tmp_path, tier1_enabled=False, tier2_provider="none", screen_blocklist="1Password, Bank")


@pytest.fixture
def app(settings):
    return create_app(settings)


@pytest.fixture
def client(app):
    with TestClient(app, **LOCAL) as c:
        yield c


def connect(client, sid="mac", hello=True):
    ws = client.websocket_connect(WS + sid)
    conn = ws.__enter__()
    config = conn.receive_json()
    assert config["type"] == "screen.config"
    if hello:
        conn.send_json(HELLO)
    return ws, conn, config


def wait_for(client, sid, pred, tries=200):
    """The hub sees a helper's message a moment after send_json returns (it runs on the app's loop)."""
    for _ in range(tries):
        state = client.get(f"/api/screen/{sid}").json()
        if pred(state):
            return state
    raise AssertionError(f"condition never held: {state}")


# ---- the endpoint ---------------------------------------------------------------
def test_helper_gets_config_and_its_hello_is_kept(client):
    ws, _conn, config = connect(client)
    try:
        assert config["blocklist"] == ["1Password", "Bank"]
        assert config["allow_automation"] is False
        state = wait_for(client, "mac", lambda s: s["displays"])
        assert state["connected"] == 1
        assert state["helper"] == "inktutor-overlay test"
        assert state["displays"][0]["frame"]["w"] == 1512
        assert client.get("/api/screen").json() == {"helpers": {"mac": 1}}
    finally:
        ws.__exit__(None, None, None)
    assert wait_for(client, "mac", lambda s: s["connected"] == 0)["displays"] == []


def test_phantom_messages_reach_the_server(client):
    ws, conn, _ = connect(client)
    try:
        conn.send_json({"type": "guide.goal", "text": "make a pivot table of sales by region"})
        conn.send_json({"type": "guide.control", "action": "pause"})
        conn.send_json({"type": "session.update", "voice": True})
        state = wait_for(client, "mac", lambda s: len(s["received"]) >= 4)
        got = [(m["type"], m.get("text") or m.get("action") or m.get("voice")) for m in state["received"][1:]]
        assert got == [
            ("guide.goal", "make a pivot table of sales by region"),
            ("guide.control", "pause"),
            ("session.update", True),
        ]
    finally:
        ws.__exit__(None, None, None)


def test_bad_message_gets_an_error_and_the_connection_stays(client):
    ws, conn, _ = connect(client, hello=False)
    try:
        conn.send_json({"type": "guide.control", "action": "click"})  # the helper can't ask the server to act
        assert conn.receive_json()["type"] == "error"
        conn.send_json({"type": "ink.update", "page_id": "p"})  # canvas messages don't belong here
        assert conn.receive_json()["type"] == "error"
        conn.send_json(HELLO)
        assert wait_for(client, "mac", lambda s: s["displays"])["connected"] == 1
    finally:
        ws.__exit__(None, None, None)


# ---- the push route -------------------------------------------------------------------
def test_push_highlight_and_caption_reach_the_helper(client):
    ws, conn, _ = connect(client)
    try:
        wait_for(client, "mac", lambda s: s["displays"])
        hl = {"id": "hl-1", "type": "highlight", "bbox": INSERT, "step_id": "s1", "ttl_ms": 8000}
        cap = {"id": "cap-1", "type": "caption", "bbox": INSERT, "text": "Click Insert in the ribbon"}
        for ev in (hl, cap):
            r = client.post("/api/screen/mac/overlay", json=ev)
            assert r.status_code == 202, r.text
            assert r.json() == {"delivered": 1, "id": ev["id"]}
        m1, m2 = conn.receive_json(), conn.receive_json()
        assert m1["type"] == m2["type"] == "overlay"
        assert m1["event"]["bbox"] == {"x": 318.0, "y": 58.0, "w": 52.0, "h": 24.0}
        assert m1["event"]["ttl_ms"] == 8000 and m1["event"]["step_id"] == "s1"
        assert m2["event"]["text"] == "Click Insert in the ribbon"
    finally:
        ws.__exit__(None, None, None)


def test_every_helper_under_the_id_gets_it_and_others_dont(client):
    a, ca, _ = connect(client, "mac")
    b, cb, _ = connect(client, "mac", hello=False)
    other, co, _ = connect(client, "other", hello=False)
    try:
        wait_for(client, "mac", lambda s: s["connected"] == 2 and s["displays"])
        r = client.post("/api/screen/mac/overlay", json={"id": "c", "type": "clear"})
        assert r.json()["delivered"] == 2
        assert ca.receive_json()["event"]["type"] == "clear"
        assert cb.receive_json()["event"]["type"] == "clear"
        co.send_json({"type": "guide.control", "action": "stop"})
        assert wait_for(client, "other", lambda s: s["received"])["received"][0]["action"] == "stop"
    finally:
        for ws in (a, b, other):
            ws.__exit__(None, None, None)


def test_push_without_a_helper_is_404(client):
    r = client.post("/api/screen/mac/overlay", json={"id": "h", "type": "highlight", "bbox": INSERT})
    assert r.status_code == 404
    assert "make overlay" in r.json()["detail"]


@pytest.mark.parametrize(
    ("event", "why"),
    [
        ({"id": "h", "type": "highlight"}, "highlight needs a bbox"),
        ({"id": "h", "type": "highlight", "bbox": {"x": 1, "y": 1, "w": 0, "h": 5}}, "positive size"),
        ({"id": "h", "type": "highlight", "bbox": {"x": 1500, "y": 900, "w": 60, "h": 30}}, "not inside any display"),
        ({"id": "a", "type": "arrow", "bbox": INSERT}, "arrow needs from_point"),
        ({"id": "a", "type": "arrow", "bbox": INSERT, "from_point": {"x": -5, "y": 10}}, "from_point is not on"),
        ({"id": "c", "type": "caption", "text": "  "}, "caption needs text"),
        ({"id": "p", "type": "phantom_state"}, "phantom_state needs state"),
        ({"id": "p", "type": "pulse"}, "pulse needs a bbox"),
    ],
)
def test_push_rules(client, event, why):
    ws, _conn, _ = connect(client)
    try:
        wait_for(client, "mac", lambda s: s["displays"])
        r = client.post("/api/screen/mac/overlay", json=event)
        assert r.status_code == 422
        assert why in r.json()["detail"]
    finally:
        ws.__exit__(None, None, None)


def test_pulse_on_a_drawn_highlight_needs_no_box(client):
    ws, conn, _ = connect(client)
    try:
        wait_for(client, "mac", lambda s: s["displays"])
        r = client.post("/api/screen/mac/overlay", json={"id": "p", "type": "pulse", "refers_to": "hl-1"})
        assert r.status_code == 202
        assert conn.receive_json()["event"]["refers_to"] == "hl-1"
    finally:
        ws.__exit__(None, None, None)


def test_push_validates_against_the_protocol(client):
    ws, _conn, _ = connect(client)
    try:
        r = client.post("/api/screen/mac/overlay", json={"id": "x", "type": "click", "bbox": INSERT})
        assert r.status_code == 422  # there is no event that acts on the screen
        r = client.post("/api/screen/mac/overlay", json={"id": "x", "type": "clear", "extra": 1})
        assert r.status_code == 422
    finally:
        ws.__exit__(None, None, None)


# ---- localhost only ---------------------------------------------------------------------
def test_tunnel_requests_are_refused_even_when_paired(app, settings):
    token = pairing.load_token(settings.data_dir)
    paired = {**TUNNEL, "cookie": f"{pairing.COOKIE}={token}"}
    with TestClient(app, **LOCAL) as c:  # cloudflared connects from 127.0.0.1, so only the headers tell
        assert c.get("/api/pairing", headers=paired).status_code == 200  # the cookie opens the app…
        with pytest.raises(WebSocketDisconnect) as e, c.websocket_connect(WS + "mac", headers=paired):
            pass
        assert e.value.code == 1008
        r = c.post("/api/screen/mac/overlay", json={"id": "c", "type": "clear"}, headers=paired)
        assert r.status_code == 403  # …but never the screen tutor
        assert c.get("/api/screen", headers={"cf-ray": "1"}).status_code == 403


def test_lan_clients_are_refused(app):
    with TestClient(app, base_url="http://192.168.1.20:8000", client=("192.168.1.30", 52000)) as c:
        with pytest.raises(WebSocketDisconnect) as e, c.websocket_connect("ws://192.168.1.20:8000/ws/screen/mac"):
            pass
        assert e.value.code == 1008
        assert c.post("/api/screen/mac/overlay", json={"id": "c", "type": "clear"}).status_code == 403
        assert c.get("/api/screen/mac").status_code == 403
        assert c.post("/api/sessions", json={"mode": "math"}).status_code == 200  # the iPad's routes stay open


def test_browsers_are_refused(client):
    with (
        pytest.raises(WebSocketDisconnect),
        client.websocket_connect(WS + "mac", headers={"origin": "http://127.0.0.1:5173"}),
    ):
        pass
    r = client.post(
        "/api/screen/mac/overlay", json={"id": "c", "type": "clear"}, headers={"origin": "https://evil.example"}
    )
    assert r.status_code == 403
    assert client.get("/api/screen", headers={"host": "evil.example:8000"}).status_code == 403  # DNS rebinding


def test_bad_session_ids_are_refused(client):
    with pytest.raises(WebSocketDisconnect), client.websocket_connect(WS + "x" * 65):
        pass
    assert client.get("/api/screen/" + "x" * 65).status_code == 422


@pytest.mark.parametrize(
    ("host", "headers", "ok"),
    [
        ("127.0.0.1", {"host": "127.0.0.1:8000"}, True),
        ("::1", {"host": "[::1]:8000"}, True),
        ("::ffff:127.0.0.1", {"host": "localhost:8000"}, True),
        ("127.0.0.1", {"host": "localhost"}, True),
        ("10.0.0.5", {"host": "127.0.0.1:8000"}, False),
        ("testclient", {"host": "127.0.0.1:8000"}, False),
        (None, {"host": "127.0.0.1:8000"}, False),
        ("127.0.0.1", {"host": "mac.local:8000"}, False),
        ("127.0.0.1", {"host": "127.0.0.1:8000", "Origin": "null"}, False),
        ("127.0.0.1", {"host": "127.0.0.1:8000", "CF-Connecting-IP": "203.0.113.7"}, False),
    ],
)
def test_refuse_reason(host, headers, ok):
    assert (refuse_reason(host, headers) is None) is ok
