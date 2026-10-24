import io
from pathlib import Path

from fastapi.testclient import TestClient

from opentutor.app import create_app
from opentutor.config import Settings
from opentutor.model import FakeModel
from opentutor.tunnel import QuickTunnel, with_token

# TestClient's ASGI scope reports server ("testserver", 80); map the tunnel port onto it.
TUNNEL = {"online_demo": True, "tunnel_port": 80}


class FakeProcess:
    def __init__(self, lines: list[str]) -> None:
        self.stderr = io.StringIO("".join(lines))
        self.returncode: int | None = None

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.returncode = 0

    def wait(self, timeout: float) -> int:  # noqa: ARG002
        return 0


def tunnel_client(settings: Settings, fake: FakeModel) -> TestClient:
    app = create_app(settings.model_copy(update=TUNNEL), fake)
    return TestClient(app, base_url="http://testserver", follow_redirects=False)


def test_ac_017_1_off_by_default(client: TestClient) -> None:
    assert client.app.state.demo_token is None  # type: ignore[attr-defined]
    assert client.get("/").status_code == 200


def test_ac_017_3_token_required(settings: Settings, fake: FakeModel) -> None:
    client = tunnel_client(settings, fake)
    assert client.get("/").status_code == 403
    assert client.get("/?k=wrong").status_code == 403


def test_ac_017_7_token_stripped_and_cookie_session_only(
    settings: Settings, fake: FakeModel
) -> None:
    client = tunnel_client(settings, fake)
    token = client.app.state.demo_token  # type: ignore[attr-defined]
    response = client.get(with_token("http://testserver/", token))
    assert response.status_code == 302
    assert "k=" not in response.headers["location"]
    cookie = response.headers["set-cookie"]
    assert "Max-Age" not in cookie
    assert "Secure" in cookie
    client.cookies.set("ot_demo", token)
    page = client.get("/")
    assert page.status_code == 200
    assert "Online demo" in page.text  # AC-017.5


def test_ac_017_4_teacher_and_health_refused_through_tunnel(
    settings: Settings, fake: FakeModel
) -> None:
    client = tunnel_client(settings, fake)
    client.cookies.set("ot_demo", client.app.state.demo_token)  # type: ignore[attr-defined]
    assert client.get("/teacher").status_code == 404
    assert client.get("/healthz").status_code == 404


def test_ac_017_7_new_token_each_start(settings: Settings, fake: FakeModel) -> None:
    first = create_app(settings.model_copy(update=TUNNEL), fake).state.demo_token
    second = create_app(settings.model_copy(update=TUNNEL), fake).state.demo_token
    assert first != second


def test_ac_017_2_quick_tunnel_reports_url() -> None:
    lines = [
        "INF Requesting new quick Tunnel\n",
        "INF |  https://brave-otter-12.trycloudflare.com  |\n",
        "INF Registered tunnel connection connIndex=0\n",
    ]
    tunnel = QuickTunnel(8001, Path("cloudflared"), popen=lambda *_a, **_k: FakeProcess(lines))
    assert tunnel.start(timeout=2) == "https://brave-otter-12.trycloudflare.com"
    tunnel.stop()
    assert not tunnel.alive()


def test_online_demo_without_cloudflared_keeps_hub_running(
    settings: Settings,
    monkeypatch,
    capsys,  # type: ignore[no-untyped-def]
) -> None:
    from opentutor import cli

    monkeypatch.setattr(cli, "find_cloudflared", lambda: None)
    cli.start_online_demo(settings, "token")
    assert "setup --online-demo" in capsys.readouterr().out


def test_download_refuses_unpinned_platform(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import pytest

    from opentutor import tunnel

    monkeypatch.setattr(tunnel.platform, "machine", lambda: "riscv64")
    with pytest.raises(RuntimeError):
        tunnel.download_cloudflared()


def test_cli_start_refuses_unsafe_settings(capsys) -> None:  # type: ignore[no-untyped-def]
    from opentutor import cli

    assert cli.main(["start", "--host", "0.0.0.0"]) == 1  # noqa: S104
    assert "all interfaces" in capsys.readouterr().err


def test_ac_012_2_qr_code_prints_on_a_cp1252_console(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import segno

    from opentutor import cli

    raw = io.BytesIO()
    console = io.TextIOWrapper(raw, encoding="cp1252")
    monkeypatch.setattr(cli.sys, "stdout", console)
    cli.utf8_output()
    segno.make("http://10.42.0.1:8000/", error="m").terminal(compact=True, border=2)
    console.flush()
    assert "█".encode() in raw.getvalue()


def test_cli_setup_runs(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from opentutor import cli

    monkeypatch.setattr(cli, "setup", lambda _model: 0)
    assert cli.main(["setup"]) == 0
