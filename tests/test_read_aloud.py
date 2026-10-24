"""Browser test for REQ-018, run with the preinstalled Playwright (RUN_E2E=1 to enable)."""

import json
import os
import shutil
import socket
import subprocess
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
import uvicorn

from opentutor.app import create_app
from opentutor.config import Settings
from opentutor.model import FakeModel

SCRIPT = Path(__file__).parent / "e2e" / "read_aloud.cjs"
REPLY = "Try x^2 + sqrt(9) = 3/4 * y - 1 first."

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_E2E") != "1" or shutil.which("node") is None,
    reason="browser test: set RUN_E2E=1 (needs node and Playwright)",
)


@pytest.fixture(scope="module")
def hub_url() -> Iterator[str]:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    app = create_app(Settings(model_backend="fake"), FakeModel(reply=REPLY))
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}/"
    server.should_exit = True
    thread.join(timeout=5)


def browser(hub_url: str, mode: str) -> dict:  # type: ignore[type-arg]
    done = subprocess.run(  # noqa: S603
        ["node", str(SCRIPT), hub_url, mode],  # noqa: S607
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    return json.loads(done.stdout.strip().splitlines()[-1])  # type: ignore[no-any-return]


def test_ac_018_1_to_5_reads_with_on_device_voice(hub_url: str) -> None:
    result = browser(hub_url, "local")
    assert result["visible"] >= 2  # greeting + reply (AC-018.1)
    assert result["labels"] == ["⏹ Stop", "🔊 Read aloud"]  # AC-018.2
    spoken = result["spoken"][0]
    assert spoken["voice"] == "Phone English"  # the on-device voice, never the cloud one
    assert "x squared" in spoken["text"]  # AC-018.4
    assert "square root of" in spoken["text"]
    assert "3 over 4" in spoken["text"]
    assert "times" in spoken["text"]
    assert "equals" in spoken["text"]
    assert result["postsDuringSpeech"] == 0  # AC-018.5


def test_ac_018_3_hidden_without_on_device_voice(hub_url: str) -> None:
    assert browser(hub_url, "none")["visible"] == 0
