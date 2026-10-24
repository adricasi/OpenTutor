import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from opentutor.app import create_app
from opentutor.config import Settings
from opentutor.model import FakeModel


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def fake() -> FakeModel:
    return FakeModel()


@pytest.fixture
def settings() -> Settings:
    return Settings(model_backend="fake", queue_update_s=0.05)


@pytest.fixture
def client(settings: Settings, fake: FakeModel) -> TestClient:
    return TestClient(create_app(settings, fake), base_url="http://hub.test")


def join(client: TestClient, nickname: str = "Ana", tutor: str = "maths") -> None:
    response = client.post("/session", data={"nickname": nickname})
    assert response.status_code == 200, response.text
    assert client.get(f"/chat/{tutor}").status_code == 200


def photo_bytes(size: tuple[int, int] = (800, 600), fmt: str = "JPEG") -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, "white").save(out, format=fmt)
    return out.getvalue()
