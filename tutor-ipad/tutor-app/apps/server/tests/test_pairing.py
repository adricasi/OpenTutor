"""Phase 10: iPad pairing (tunnel token gate, quick-tunnel URL parsing, LAN URLs) and the demo script endpoint."""

import io
import os

import pytest
from fastapi.testclient import TestClient
from inktutor_server import pairing
from inktutor_server.app import create_app
from inktutor_server.config import Settings
from inktutor_server.demo import DEFAULT_STEM
from starlette.websockets import WebSocketDisconnect

TUNNEL = {"host": "abc-def.trycloudflare.com", "cf-ray": "8f00-MAD", "cf-connecting-ip": "203.0.113.7"}


@pytest.fixture
def settings(tmp_path):
    return Settings(data_dir=tmp_path, tier1_enabled=False, tier2_provider="none")


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as c:
        yield c


# ---- the gate -----------------------------------------------------------------
def test_lan_and_localhost_stay_open(client):
    assert client.get("/api/pairing").status_code == 200
    assert client.post("/api/sessions", json={"mode": "math"}).status_code == 200


@pytest.mark.parametrize(
    "headers",
    [TUNNEL, {"cf-ray": "8f00-MAD"}, {"cf-connecting-ip": "203.0.113.7"}, {"host": "x-y.trycloudflare.com"}],
    ids=["all", "cf-ray", "cf-connecting-ip", "host"],
)
def test_tunnel_requests_need_the_token(client, settings, headers):
    pairing.load_token(settings.data_dir)
    r = client.get("/api/pairing", headers=headers)
    assert r.status_code == 403
    assert "Scan the QR code again" in r.text


def test_tunnel_is_closed_before_make_pair_ever_ran(client):
    assert client.get("/", headers=TUNNEL).status_code == 403


def test_healthz_is_open_through_the_tunnel(client):
    assert client.get("/healthz", headers=TUNNEL).status_code == 200  # the reachability probe


def test_token_in_query_sets_httponly_cookie_then_cookie_works(client, settings):
    token = pairing.load_token(settings.data_dir)
    assert client.get("/api/pairing", headers=TUNNEL, params={"k": "wrong"}).status_code == 403
    r = client.get("/", headers=TUNNEL, params={"demo": DEFAULT_STEM, "k": token}, follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == f"/?demo={DEFAULT_STEM}"  # the token leaves the address bar
    cookie = r.headers["set-cookie"]
    assert f"{pairing.COOKIE}={token}" in cookie
    for attr in ("HttpOnly", "Secure", "SameSite=Lax", "Path=/"):
        assert attr in cookie
    client.cookies.clear()
    ok = client.get("/api/pairing", headers={**TUNNEL, "cookie": f"{pairing.COOKIE}={token}"})
    assert ok.status_code == 200
    bad = client.get("/api/pairing", headers={**TUNNEL, "cookie": f"{pairing.COOKIE}=nope"})
    assert bad.status_code == 403


def test_rotated_token_locks_out_old_cookies(client, settings):
    old = pairing.load_token(settings.data_dir)
    assert client.get("/api/pairing", headers={**TUNNEL, "cookie": f"{pairing.COOKIE}={old}"}).status_code == 200
    new = pairing.load_token(settings.data_dir, new=True)
    assert new != old
    assert client.get("/api/pairing", headers={**TUNNEL, "cookie": f"{pairing.COOKIE}={old}"}).status_code == 403


def test_websocket_through_tunnel_needs_cookie(client, settings):
    token = pairing.load_token(settings.data_dir)
    sid = client.post("/api/sessions", json={"mode": "math"}).json()["id"]
    with pytest.raises(WebSocketDisconnect), client.websocket_connect(f"/ws/session/{sid}", headers=TUNNEL) as ws:
        ws.receive_json()
    hdrs = {**TUNNEL, "cookie": f"{pairing.COOKIE}={token}"}
    with client.websocket_connect(f"/ws/session/{sid}", headers=hdrs) as ws:
        assert ws.receive_json()["type"] == "session"


# ---- /api/pairing ---------------------------------------------------------------
def test_pairing_info_without_tunnel(client, monkeypatch):
    monkeypatch.setattr(pairing, "lan_ip", lambda: "192.168.1.23")
    monkeypatch.setattr(pairing, "local_hostname", lambda: "studio")
    info = client.get("/api/pairing").json()
    assert info["tunnel"] is None
    assert info["lan"]["url"] == "http://192.168.1.23:8000/"
    assert info["lan"]["mdns"] == "http://studio.local:8000/"
    assert info["lan"]["qr"].startswith("data:image/svg+xml")
    assert "microphone" in info["lan"]["note"]
    assert "make pair" in info["hint"]


def test_pairing_info_with_live_tunnel(client, settings):
    token = pairing.load_token(settings.data_dir)
    pairing.write_state(settings.data_dir, url="https://abc-def.trycloudflare.com", reachable=True, bonjour=True)
    info = client.get("/api/pairing").json()
    assert info["tunnel"]["url"] == f"https://abc-def.trycloudflare.com/?k={token}"
    assert info["tunnel"]["reachable"] is True
    assert info["tunnel"]["qr"].startswith("data:image/svg+xml")
    assert info["bonjour"] == "Ink Tutor"
    pairing.clear_state(settings.data_dir)
    assert client.get("/api/pairing").json()["tunnel"] is None


def test_stale_state_from_a_killed_pair_is_ignored(settings):
    pairing.write_state(settings.data_dir, url="https://old.trycloudflare.com")
    path = pairing.pairing_dir(settings.data_dir) / "state.json"
    path.write_text(path.read_text().replace(f'"pid": {os.getpid()}', '"pid": 999999'))
    assert pairing.read_state(settings.data_dir) == {}


# ---- LAN URLs -----------------------------------------------------------------
def test_lan_urls():
    assert pairing.lan_url(8000, "10.0.0.5") == "http://10.0.0.5:8000/"
    assert pairing.mdns_url(8000, "my-mac") == "http://my-mac.local:8000/"
    ip = pairing.lan_ip()
    assert ip.count(".") == 3 and all(p.isdigit() for p in ip.split("."))


def test_with_token_keeps_other_params():
    assert pairing.with_token("https://a.trycloudflare.com/", "T") == "https://a.trycloudflare.com/?k=T"
    assert pairing.with_token("https://a.trycloudflare.com/?demo=x", "T") == "https://a.trycloudflare.com/?demo=x&k=T"


# ---- quick tunnel with a fake cloudflared -------------------------------------------
class FakeProc:
    def __init__(self, lines, exit_code=None):
        self.stderr = io.StringIO("".join(lines))
        self.returncode = exit_code
        self.args = None
        self.terminated = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = -15

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        self.returncode = -9


CLOUDFLARED_LOG = [
    "2026-10-24T10:00:00Z INF Requesting new quick Tunnel on trycloudflare.com...\n",
    "2026-10-24T10:00:01Z INF +------------------------------------------------------------+\n",
    "2026-10-24T10:00:01Z INF |  Your quick Tunnel has been created! Visit it at:  |\n",
    "2026-10-24T10:00:01Z INF |  https://lemon-tiger-42-bay.trycloudflare.com      |\n",
    "2026-10-24T10:00:02Z INF Registered tunnel connection connIndex=0 connection=abc location=mad\n",
]


def test_quick_tunnel_parses_url_from_cloudflared(tmp_path):
    procs = []

    def popen(args, **kw):
        procs.append(p := FakeProc(CLOUDFLARED_LOG))
        p.args = args
        return p

    t = pairing.QuickTunnel(8000, tmp_path / "cf.log", popen=popen, binary=tmp_path / "cloudflared")
    assert t.start(timeout=5) == "https://lemon-tiger-42-bay.trycloudflare.com"
    assert procs[0].args[-2:] == ["--url", "http://127.0.0.1:8000"]
    assert "Registered tunnel connection" in (tmp_path / "cf.log").read_text()
    assert t.alive()
    t.stop()
    assert procs[0].terminated and not t.alive()


def test_quick_tunnel_waits_for_registration(tmp_path):
    t = pairing.QuickTunnel(
        8000, tmp_path / "cf.log", popen=lambda a, **k: FakeProc(CLOUDFLARED_LOG[:4]), binary=tmp_path
    )
    with pytest.raises(RuntimeError, match="did not come up"):
        t.start(timeout=0.5)  # URL printed but no connection registered: not usable yet


def test_quick_tunnel_reports_cloudflared_exit(tmp_path):
    t = pairing.QuickTunnel(8000, tmp_path / "cf.log", popen=lambda a, **k: FakeProc(["boom\n"], 1), binary=tmp_path)
    with pytest.raises(RuntimeError, match="exited"):
        t.start(timeout=5)


# ---- demo script ------------------------------------------------------------------
def test_demo_script_endpoint(client):
    d = client.get(f"/api/demo/{DEFAULT_STEM}").json()
    assert d["mode"] == "math"
    assert d["problem"].startswith("Compute the definite integrals")  # from the annotation sidecar
    assert d["page"] == {"width": 1194, "height": 628}
    types = {e["type"] for e in d["events"]}
    assert {"stroke.start", "stroke.points", "stroke.end", "erase"} <= types
    ts = [e["t"] for e in d["events"]]
    assert ts == sorted(ts)
    assert client.get("/api/demo/nope").status_code == 404
    assert client.get(f"/api/demo/{DEFAULT_STEM}.json").status_code == 404  # stems only, never a path
