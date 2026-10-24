"""Model clients: Ollama on loopback (ADR-0004) and a fake backend for tests and UI work."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any, Protocol

import httpx
import ollama
from pydantic import BaseModel


class ModelError(Exception):
    pass


Message = dict[str, Any]


class ModelClient(Protocol):
    def stream(self, messages: list[Message], max_tokens: int) -> AsyncIterator[str]: ...

    async def structured(
        self, messages: list[Message], schema: type[BaseModel], max_tokens: int
    ) -> str: ...

    async def health(self) -> tuple[bool, str]: ...


class OllamaModel:
    def __init__(self, url: str, model: str, digest: str = "", keep_alive: str = "8h") -> None:
        self._client = ollama.AsyncClient(host=url)
        self.model, self.digest, self.keep_alive = model, digest, keep_alive

    async def warm_up(self) -> None:
        """Load the model now so the first student doesn't wait for it (REQ-012)."""
        try:
            await self._client.chat(
                model=self.model,
                messages=[{"role": "user", "content": "Hi"}],
                options={"num_predict": 1},
                keep_alive=self.keep_alive,
            )
        except (ollama.ResponseError, httpx.HTTPError, ConnectionError, OSError) as error:
            raise ModelError(str(error)) from error

    async def stream(self, messages: list[Message], max_tokens: int) -> AsyncIterator[str]:
        try:
            parts = await self._client.chat(
                model=self.model,
                messages=messages,
                stream=True,
                options={"temperature": 0.3, "num_predict": max_tokens},
                keep_alive=self.keep_alive,
            )
            async for part in parts:
                if part.message.content:
                    yield part.message.content
        except (ollama.ResponseError, httpx.HTTPError, ConnectionError, OSError) as error:
            raise ModelError(str(error)) from error

    async def structured(
        self, messages: list[Message], schema: type[BaseModel], max_tokens: int
    ) -> str:
        try:
            response = await self._client.chat(
                model=self.model,
                messages=messages,
                format=schema.model_json_schema(),
                options={"temperature": 0, "num_predict": max_tokens},
                keep_alive=self.keep_alive,
            )
        except (ollama.ResponseError, httpx.HTTPError, ConnectionError, OSError) as error:
            raise ModelError(str(error)) from error
        return response.message.content or ""

    async def health(self) -> tuple[bool, str]:
        """(ok, message): runtime reachable, model pulled, digest matches when configured."""
        try:
            listing = await self._client.list()
        except (ollama.ResponseError, httpx.HTTPError, ConnectionError, OSError):
            return False, "Ollama is not running. Start it with: sudo systemctl start ollama"
        for entry in listing.models:
            if entry.model == self.model or entry.model == f"{self.model}:latest":
                if self.digest and not (entry.digest or "").startswith(
                    self.digest
                ):  # list shows a prefix
                    return False, f"Model {self.model} digest {entry.digest} != configured digest"
                return True, f"{self.model} ready"
        return False, f"Model {self.model} is not downloaded. Run: ollama pull {self.model}"


class FakeModel:
    """Deterministic replies; `structured_replies` maps schema name → JSON text."""

    def __init__(self, reply: str = "Let's look at it together. What do you notice first?") -> None:
        self.reply = reply
        self.structured_replies: dict[str, list[str]] = {}
        self.calls: list[list[Message]] = []
        self.delay = 0.0

    async def stream(self, messages: list[Message], max_tokens: int) -> AsyncIterator[str]:  # noqa: ARG002
        self.calls.append(messages)
        for word in self.reply.split(" "):
            await asyncio.sleep(self.delay)
            yield word + " "

    async def structured(
        self,
        messages: list[Message],
        schema: type[BaseModel],
        max_tokens: int,  # noqa: ARG002
    ) -> str:
        self.calls.append(messages)
        await asyncio.sleep(self.delay)
        queued = self.structured_replies.get(schema.__name__)
        if queued:
            return queued.pop(0)
        raise ModelError(f"no fake reply queued for {schema.__name__}")

    async def health(self) -> tuple[bool, str]:
        return True, "fake model ready"
