import json

from fastapi.testclient import TestClient

from opentutor.app import create_app
from opentutor.config import Settings
from opentutor.insights import TeacherGate
from opentutor.model import FakeModel
from tests.conftest import join


def local_client(settings: Settings, fake: FakeModel) -> TestClient:
    allowed = settings.model_copy(update={"teacher_from_network": True})
    return TestClient(create_app(allowed, fake), base_url="http://hub.test")


def test_ac_013_1_teacher_view_hidden_from_network_by_default(client: TestClient) -> None:
    assert client.get("/teacher").status_code == 404
    assert client.post("/teacher/login", data={"pin": "000000"}).status_code == 404


def test_ac_013_1_pin_has_six_digits() -> None:
    pin = TeacherGate().pin
    assert len(pin) == 6
    assert pin.isdigit()


def test_ac_013_2_and_3_aggregated_insights(settings: Settings, fake: FakeModel) -> None:
    client = local_client(settings, fake)
    join(client, "Ana")
    client.post("/api/messages", data={"intent": "ask", "text": "Solve 2x + 3 = 11"})
    client.post("/api/messages", data={"intent": "hint"})
    verdict = {"verdict": "not_yet_correct", "explanation": "Look again."}
    fake.structured_replies["Verdict"] = [json.dumps(verdict)]
    client.post("/api/messages", data={"intent": "check", "text": "x = 7"})
    pin = client.app.state.teacher.pin  # type: ignore[attr-defined]
    page = client.post("/teacher/login", data={"pin": pin})
    assert page.status_code == 200
    assert "Hints asked: 1" in page.text
    assert "<p>Not yet</p><strong>1</strong>" in page.text
    assert "Ana" not in page.text.split("<main")[1]  # no nicknames
    assert "Solve 2x" not in page.text  # no message text


def test_ac_013_4_lockout_per_address() -> None:
    clock = [0.0]
    gate = TeacherGate(clock=lambda: clock[0])
    wrong = "x" if gate.pin != "x" else "y"
    for _ in range(5):
        assert not gate.login("10.42.0.5", wrong)
    assert not gate.login("10.42.0.5", gate.pin)  # locked even with the right PIN
    assert gate.login("10.42.0.6", gate.pin)  # other addresses unaffected
    clock[0] = 301
    assert gate.login("10.42.0.5", gate.pin)


def test_ac_013_5_restart_resets_insights(settings: Settings, fake: FakeModel) -> None:
    first = create_app(settings, fake)
    first.state.insights.hint("maths")
    assert not create_app(settings, fake).state.insights.by_tutor
