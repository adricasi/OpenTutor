"""REQ-021: open the iPad tutor from the hub (GET /ipad, OPENTUTOR_IPAD_URL)."""

import pytest
from fastapi.testclient import TestClient

from opentutor import cli
from opentutor.app import create_app
from opentutor.config import Settings
from opentutor.model import FakeModel
from tests.conftest import join

IPAD = "https://brave-otter-12.trycloudflare.com/?k=abc"


def hub(settings: Settings, fake: FakeModel) -> TestClient:
    return TestClient(
        create_app(settings, fake), base_url="http://hub.test", follow_redirects=False
    )


def test_ac_021_1_join_page_links_to_ipad(client: TestClient) -> None:
    page = client.get("/")
    assert 'href="/ipad"' in page.text
    assert "On an iPad? Write with Apple Pencil" in page.text
    assert page.text.count("<svg") == page.text.count("</svg>")


def test_ac_021_1_subjects_page_links_to_ipad(client: TestClient) -> None:
    join(client)
    page = client.get("/subjects")
    card = page.text.split('class="ot-ipad-card"')[1].split("</a>")[0]
    assert 'href="/ipad"' in page.text
    assert "Write on an iPad" in card
    assert "Apple Pencil" in card


def test_ac_021_2_redirects_to_ipad_tutor_without_session(fake: FakeModel) -> None:
    client = hub(Settings(model_backend="fake", ipad_url=IPAD), fake)
    response = client.get("/ipad")  # no session cookie
    assert response.status_code == 303
    assert response.headers["location"] == IPAD
    assert response.headers["referrer-policy"] == "same-origin"  # no hub URL sent to it


def test_ac_021_2_redirects_with_a_session_too(fake: FakeModel) -> None:
    client = hub(Settings(model_backend="fake", ipad_url="http://mac.local:8000"), fake)
    client.post("/session", data={"nickname": "Ana"})
    response = client.get("/ipad")
    assert response.status_code == 303
    assert response.headers["location"] == "http://mac.local:8000"


def test_ac_021_2_redirects_through_online_demo_link(fake: FakeModel) -> None:
    # TestClient's ASGI scope reports server port 80; treat it as the tunnel listener.
    tunnel = Settings(model_backend="fake", ipad_url=IPAD, online_demo=True, tunnel_port=80)
    client = TestClient(create_app(tunnel, fake), follow_redirects=False)
    assert client.get("/ipad").status_code == 403  # the demo token gate still applies
    client.cookies.set("ot_demo", client.app.state.demo_token)  # type: ignore[attr-defined]
    response = client.get("/ipad")
    assert response.status_code == 303
    assert response.headers["location"] == IPAD


def test_ac_021_3_help_page_when_not_set(fake: FakeModel) -> None:
    page = hub(Settings(model_backend="fake", ipad_url=""), fake).get("/ipad")  # no session
    assert page.status_code == 200
    assert "connected yet" in page.text
    assert "ask your teacher" in page.text
    assert "make ipad" in page.text
    assert "OPENTUTOR_IPAD_URL" in page.text
    assert 'href="/"' in page.text
    assert "default-src 'self'" in page.headers["content-security-policy"]
    assert page.text.count("<svg") == page.text.count("</svg>")


@pytest.mark.parametrize(
    "value",
    [
        "javascript:alert(1)",
        "ftp://mac.local/",
        "http://[::1",
        "//mac.local:8000",
        "mac.local:8000",
        "/subjects",
        "http://",
        "https://mac .local",
        "http://mac.local\r\nset-cookie: x=1",
        "  ",
    ],
)
def test_ac_021_4_invalid_url_is_ignored(value: str, fake: FakeModel) -> None:
    settings = Settings(model_backend="fake", ipad_url=value)
    assert settings.ipad_url == ""
    response = hub(settings, fake).get("/ipad")
    assert response.status_code == 200
    assert "connected yet" in response.text


def test_ac_021_4_set_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENTUTOR_IPAD_URL", " http://mac.local:8000/ ")
    assert Settings().ipad_url == "http://mac.local:8000/"
    monkeypatch.setenv("OPENTUTOR_IPAD_URL", "mac.local:8000")
    assert Settings().ipad_url == ""


def test_ac_021_5_start_prints_ipad_tutor(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("OPENTUTOR_IPAD_URL", "http://mac.local:8000")
    monkeypatch.setattr(cli, "serve", lambda *_args, **_kwargs: None)
    assert cli.main(["start", "--dev"]) == 0
    assert "iPad tutor: http://mac.local:8000" in capsys.readouterr().out


def test_ac_021_5_start_without_ipad_tutor_prints_nothing_extra(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("OPENTUTOR_IPAD_URL", raising=False)
    monkeypatch.setattr(cli, "serve", lambda *_args, **_kwargs: None)
    assert cli.main(["start", "--dev"]) == 0
    assert "iPad tutor" not in capsys.readouterr().out
