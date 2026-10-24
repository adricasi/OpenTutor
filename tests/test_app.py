import json
import time

from fastapi.testclient import TestClient
from starlette.formparsers import MultiPartParser

from opentutor.cli import check_start
from opentutor.config import Settings
from opentutor.model import FakeModel
from tests.conftest import join, photo_bytes


def events(response) -> list[dict]:  # type: ignore[no-untyped-def]
    return [json.loads(line) for line in response.text.splitlines() if line]


def send(client: TestClient, intent: str = "ask", text: str = "", image: bytes | None = None):  # type: ignore[no-untyped-def]
    files = {"image": ("photo.jpg", image, "image/jpeg")} if image else None
    return client.post("/api/messages", data={"intent": intent, "text": text}, files=files)


def analysis(count: int = 1, readable: bool = True) -> str:
    return json.dumps(
        {
            "readable": readable,
            "exercise_count": count,
            "exercise_text": "2x+3=11",
            "topic": "equations",
        }
    )


def test_ac_001_1_join_page_is_self_contained(client: TestClient) -> None:
    page = client.get("/")
    assert page.status_code == 200
    assert "http" not in page.text.split("<body>")[0].replace('content="width', "")
    assert "default-src 'self'" in page.headers["content-security-policy"]


def test_ac_001_6_reload_resumes_conversation(client: TestClient) -> None:
    join(client)
    send(client, text="Solve 2x + 3 = 11")
    page = client.get("/chat/maths")
    assert "Solve 2x + 3 = 11" in page.text


def test_ac_002_3_switching_subject_starts_new_conversation(client: TestClient) -> None:
    join(client)
    send(client, text="Solve 2x + 3 = 11")
    assert "Solve 2x" not in client.get("/chat/science").text


def test_ac_003_1_reply_streams_as_events(client: TestClient) -> None:
    join(client)
    got = events(send(client, text="Solve 2x + 3 = 11"))
    kinds = [e["type"] for e in got]
    assert kinds[0] == "started"
    assert kinds[-1] == "done"
    assert kinds.count("token") > 1


def test_ac_003_2_message_limits(client: TestClient) -> None:
    join(client)
    assert send(client, text="").status_code == 400
    assert send(client, text="x" * 2001).status_code == 400


def test_ac_003_3_model_timeout_gives_retryable_error(settings: Settings, fake: FakeModel) -> None:
    from opentutor.app import create_app

    fake.delay = 0.2
    client = TestClient(
        create_app(settings.model_copy(update={"first_output_timeout_s": 0.05}), fake)
    )
    join(client)
    got = events(send(client, text="hello"))
    assert got[-1]["type"] == "error"
    assert got[-1]["retryable"] is True
    assert send(client, text="again").status_code == 200  # conversation still usable


def test_ac_004_2_photo_question(client: TestClient, fake: FakeModel) -> None:
    join(client)
    fake.structured_replies["ExerciseAnalysis"] = [analysis()]
    got = events(send(client, image=photo_bytes()))
    assert any(e["type"] == "token" for e in got)
    assert fake.calls[0][-1]["images"]  # the image went to the model


def test_ac_004_3_wrong_format_rejected(client: TestClient) -> None:
    join(client)
    files = {"image": ("x.gif", b"GIF89a....", "image/gif")}
    assert client.post("/api/messages", data={"intent": "ask"}, files=files).status_code == 400


def test_ac_004_4_unreadable_photo(client: TestClient, fake: FakeModel) -> None:
    join(client)
    fake.structured_replies["ExerciseAnalysis"] = [analysis(readable=False)]
    got = events(send(client, image=photo_bytes()))
    assert any(e.get("code") == "unreadable_image" for e in got)


def test_ac_004_5_several_exercises_asks_which(client: TestClient, fake: FakeModel) -> None:
    join(client)
    fake.structured_replies["ExerciseAnalysis"] = [analysis(count=3)]
    got = events(send(client, image=photo_bytes()))
    assert any(e.get("code") == "several_exercises" for e in got)


def test_ac_004_6_uploads_stay_in_memory(settings: Settings) -> None:
    assert MultiPartParser.spool_max_size > settings.max_upload_bytes


def last_reply_request(fake: FakeModel) -> str:
    """The last tutoring request (background topic classification calls are skipped)."""
    calls = [c for c in fake.calls if "[Hub instruction]" in str(c[-1]["content"])]
    return str(calls[-1][-1]["content"])


def test_ac_005_2_hint_ladder_levels(client: TestClient, fake: FakeModel) -> None:
    join(client)
    send(client, text="Solve 2x + 3 = 11")
    assert "hint level 1" in last_reply_request(fake)
    send(client, intent="hint")
    assert "hint level 2" in last_reply_request(fake)
    send(client, intent="hint")
    send(client, intent="hint")
    assert "hint level 3" in last_reply_request(fake)
    got = events(send(client, intent="answer"))
    assert "hint level 4" in last_reply_request(fake)
    assert any(e.get("code") == "mistakes_reminder" for e in got)


def test_ac_005_5_exercise_kept_when_old_turns_dropped(settings: Settings, fake: FakeModel) -> None:
    from opentutor.app import create_app

    client = TestClient(create_app(settings.model_copy(update={"turns_kept": 2}), fake))
    join(client)
    send(client, text="Solve 2x + 3 = 11")
    for _ in range(4):
        send(client, text="more")
    contents = " ".join(str(m["content"]) for m in fake.calls[-1])
    assert "Solve 2x + 3 = 11" in contents


def test_ac_006_1_check_returns_verdict(client: TestClient, fake: FakeModel) -> None:
    join(client)
    send(client, text="Solve 2x + 3 = 11")
    fake.structured_replies["Verdict"] = [
        "not json",  # first answer invalid: retried once (AC-006.4)
        json.dumps(
            {
                "verdict": "not_yet_correct",
                "explanation": "Check step 2.",
                "first_error_step": "2x = 14",
                "hint": "11 - 3?",
            }
        ),
    ]
    got = events(send(client, intent="check", text="2x = 14, x = 7"))
    verdict = next(e for e in got if e["type"] == "verdict")
    assert verdict["verdict"] == "not_yet_correct"


def test_ac_006_4_check_fails_cleanly(client: TestClient, fake: FakeModel) -> None:
    join(client)
    send(client, text="Solve 2x + 3 = 11")
    fake.structured_replies["Verdict"] = ["bad", "still bad"]
    got = events(send(client, intent="check", text="x = 4"))
    assert any(e.get("code") == "check_failed" for e in got)


def test_ac_007_1_three_practice_exercises(client: TestClient, fake: FakeModel) -> None:
    join(client)
    send(client, text="Solve 2x + 3 = 11")
    fake.structured_replies["PracticeSet"] = [
        json.dumps(
            {"exercises": [{"number": i, "text": f"Solve {i}x = {2 * i}"} for i in (1, 2, 3)]}
        )
    ]
    got = events(send(client, intent="practice"))
    assert len(next(e for e in got if e["type"] == "practice")["exercises"]) == 3


def test_ac_007_5_practice_without_exercise_asks_topic(client: TestClient) -> None:
    join(client)
    got = events(send(client, intent="practice"))
    assert got[1]["code"] == "no_exercise"


def test_ac_011_1_finish_deletes_session(client: TestClient) -> None:
    join(client)
    client.post("/session/end")
    assert client.get("/subjects").url.path == "/"
    assert len(client.app.state.store) == 0  # type: ignore[attr-defined]


def test_cross_origin_post_refused(client: TestClient) -> None:
    response = client.post(
        "/session", data={"nickname": "Eve"}, headers={"origin": "http://evil.test"}
    )
    assert response.status_code == 403


def test_nfr_001_refuses_all_interfaces() -> None:
    assert check_start(Settings(model_backend="fake", host="0.0.0.0"))  # noqa: S104


def test_healthz_loopback_only(client: TestClient) -> None:
    assert client.get("/healthz").status_code == 404  # test client address is not loopback


def test_browser_form_post_with_same_origin_is_accepted(client: TestClient) -> None:
    assert client.get("/").headers["referrer-policy"] == "same-origin"
    response = client.post(
        "/session", data={"nickname": "Ana"}, headers={"origin": "http://hub.test"}
    )
    assert response.status_code == 200


def test_ac_007_2_each_practice_answer_checked_separately(
    client: TestClient, fake: FakeModel
) -> None:
    join(client)
    send(client, text="Solve 2x + 3 = 11")
    fake.structured_replies["PracticeSet"] = [
        json.dumps(
            {"exercises": [{"number": i, "text": f"Solve x + {i} = {i + 5}"} for i in (1, 2, 3)]}
        )
    ]
    send(client, intent="practice")
    explain = json.dumps({"verdict": "correct", "explanation": "Well done."})
    fake.structured_replies["Verdict"] = [explain, explain]
    got = events(send(client, intent="check", text="1. x = 5\n3. x = 4"))
    verdicts = [e for e in got if e["type"] == "verdict"]
    assert [v["practice_number"] for v in verdicts] == [1, 3]
    assert verdicts[0]["verdict"] == "correct"  # x + 1 = 6 -> x = 5 (maths checker)
    assert verdicts[1]["verdict"] == "not_yet_correct"  # x + 3 = 8 -> x = 5, not 4


def test_ac_013_3_typed_exercise_topic_classified(settings: Settings, fake: FakeModel) -> None:
    from opentutor.app import create_app

    app = create_app(settings.model_copy(update={"teacher_from_network": True}), fake)
    fake.structured_replies["TopicChoice"] = [json.dumps({"topic": "Equations"})]
    with TestClient(app, base_url="http://hub.test") as client:  # runs background tasks
        join(client)
        send(client, text="Solve 2x + 3 = 11")
        for _ in range(50):
            if app.state.insights.by_tutor["maths"].topics:
                break
            time.sleep(0.02)
    assert app.state.insights.by_tutor["maths"].topics == {"equations": 1}
