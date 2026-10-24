"""Browser test for REQ-019 (write by hand), with the preinstalled Playwright (RUN_E2E=1)."""

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
from pydantic import BaseModel

from opentutor.app import create_app
from opentutor.config import Settings
from opentutor.model import FakeModel, Message

SCRIPT = Path(__file__).parent / "e2e" / "handwriting.cjs"

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_E2E") != "1" or shutil.which("node") is None,
    reason="browser test: set RUN_E2E=1 (needs node and Playwright)",
)


class ReadsSeven(FakeModel):
    """Reads the handwriting as "x = 7" and explains whatever verdict the checker fixed."""

    async def structured(
        self,
        messages: list[Message],
        schema: type[BaseModel],
        max_tokens: int,  # noqa: ARG002
    ) -> str:
        self.calls.append(messages)
        if schema.__name__ == "HandwritingReading":
            return json.dumps({"readable": True, "text": "x = 7"})
        return json.dumps({"verdict": "correct", "explanation": "Look at your first step."})


@pytest.fixture(scope="module")
def hub_url() -> Iterator[str]:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(Settings(model_backend="fake"), ReadsSeven()),
            host="127.0.0.1",
            port=port,
            log_level="error",
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}/"
    server.should_exit = True
    thread.join(timeout=5)


def test_ac_019_1_to_3_write_use_and_check(hub_url: str) -> None:
    done = subprocess.run(  # noqa: S603
        ["node", str(SCRIPT), hub_url, os.environ.get("E2E_SCREENSHOT", "")],  # noqa: S607
        capture_output=True,
        text=True,
        timeout=90,
        check=True,
    )
    result = json.loads(done.stdout.strip().splitlines()[-1])
    assert result["errors"] == []
    assert result["previewLabel"] == "Your writing"  # AC-019.2
    assert result["padHiddenAfterUse"]
    assert "x = 7" in result["reading"]  # AC-019.3: only sent with the handwriting flag
    assert result["verdict"] == "Not yet correct"  # the maths checker decided on the read text
