"""Browser tests for REQ-020 (speak) and AC-018.3 (listen), preinstalled Playwright (RUN_E2E=1)."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.test_read_aloud import browser, hub_url  # noqa: F401 - shared server fixture

SCRIPT = Path(__file__).parent / "e2e" / "speak.cjs"

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_E2E") != "1" or shutil.which("node") is None,
    reason="browser test: set RUN_E2E=1 (needs node and Playwright)",
)


def speak(hub_url: str, mode: str) -> dict:  # type: ignore[type-arg]  # noqa: F811
    done = subprocess.run(  # noqa: S603
        ["node", str(SCRIPT), hub_url, mode],  # noqa: S607
        capture_output=True,
        text=True,
        timeout=90,
        check=True,
    )
    return json.loads(done.stdout.strip().splitlines()[-1])  # type: ignore[no-any-return]


def test_ac_020_2_speech_fills_the_box_for_checking(hub_url: str) -> None:  # noqa: F811
    result = speak(hub_url, "works")
    assert result["visible"]  # AC-020.1
    assert result["text"] == "solve two x plus three equals eleven"  # not sent yet
    assert not result["listeningAfter"]
    assert any("never records your voice" in n for n in result["notices"])  # AC-020.4
    assert not result["audioPosted"]  # AC-020.5
    assert result["errors"] == []


@pytest.mark.parametrize("mode", ["blocked", "missing", "insecure", "silent"])
def test_ac_020_3_falls_back_to_keyboard_dictation(hub_url: str, mode: str) -> None:  # noqa: F811
    result = speak(hub_url, mode)
    assert any("microphone on your keyboard" in n for n in result["notices"])
    assert result["focused"] == "text"
    assert not result["listeningAfter"]  # "silent": the recogniser never started
    assert result["text"] == ""
    assert result["errors"] == []


def test_ac_020_3_plain_http_page_never_starts_recognition(hub_url: str) -> None:  # noqa: F811
    result = speak(hub_url, "insecure")
    assert not result["secure"]
    assert not result["listeningSoon"]


def test_ac_020_2_tapping_again_stops_a_recogniser_that_never_started(hub_url: str) -> None:  # noqa: F811
    result = speak(hub_url, "silent-stop")
    assert result["listeningSoon"]
    assert not result["listeningAfter"]
    assert result["errors"] == []


def test_ac_018_3_listen_shown_and_explains_without_offline_voice(hub_url: str) -> None:  # noqa: F811
    result = browser(hub_url, "none")
    assert result["visible"] >= 1
