"""Phase 5: the three tier-2 providers behind one interface, tested at the HTTP level (no keys, no spend).

Mocked transports check what each provider sends (and that only tutoring data leaves the Mac) and that
streamed answers come back as the same JSON-lines objects.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import anthropic
import httpx
import httpx2  # the anthropic SDK 2.x transport
import openai
import pytest
from inktutor_server.tutor.base import LineContext, ReviewContext
from inktutor_server.tutor.cloud import AnthropicTutor, CloudRefusal, OpenAITutor
from inktutor_server.tutor.local import LocalMLX

ANSWER = [
    '{"type":"speak","line_id":"L1","text":"check the power rule"}\n',
    '{"type":"explain","line_id":"L1","text":"the exponent goes up by one: $\\\\frac{x^4}{4}$"}\n',
]


def ctx() -> ReviewContext:
    return ReviewContext(
        mode="math",
        problem="Compute ∫₀¹ x³ dx",
        rubric="mark the first wrong step",
        lines=[
            LineContext(
                id="L1",
                latex=r"\int_0^1 x^3dx=\frac{x^5}{3}",
                text=None,
                kind="math",
                confidence=0.8,
                checks=["wrong antiderivative"],
                checker_ok=False,
            )
        ],
        changed=["L1"],
        images=[("L1", b"\x89PNG fake")],
        prior=[],
        dismissed=[],
    )


def sse(events: list[tuple[str, dict[str, Any]]]) -> bytes:
    return "".join(f"event: {e}\ndata: {json.dumps(d)}\n\n" for e, d in events).encode()


def anthropic_stream(chunks: list[str], stop: str = "end_turn") -> bytes:
    msg = {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": "claude-sonnet-5-5",
        "content": [],
        "stop_reason": None,
        "stop_sequence": None,
        "usage": {"input_tokens": 10, "output_tokens": 1},
    }
    ev = [
        ("message_start", {"type": "message_start", "message": msg}),
        (
            "content_block_start",
            {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        ),
    ]
    ev += [
        ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": c}})
        for c in chunks
    ]
    ev += [
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        (
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": stop, "stop_sequence": None},
                "usage": {"output_tokens": 20},
            },
        ),
        ("message_stop", {"type": "message_stop"}),
    ]
    return sse(ev)


async def collect(agen) -> list[dict[str, Any]]:
    return [o async for o in agen]


def test_anthropic_request_shape_and_streaming():
    seen: dict[str, Any] = {}

    def handler(req: httpx2.Request) -> httpx2.Response:
        seen["url"], seen["headers"], seen["body"] = str(req.url), req.headers, json.loads(req.content)
        # split a JSON line across chunks, as streaming does
        chunks = [ANSWER[0][:20], ANSWER[0][20:], ANSWER[1]]
        return httpx2.Response(200, content=anthropic_stream(chunks), headers={"content-type": "text/event-stream"})

    client = anthropic.AsyncAnthropic(
        api_key="test", http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    )
    out = asyncio.run(collect(AnthropicTutor("test", "claude-sonnet-5-5", client).review(ctx())))
    assert [o["type"] for o in out] == ["speak", "explain"]
    assert out[1]["text"].endswith("$\\frac{x^4}{4}$")
    b, h = seen["body"], seen["headers"]
    assert seen["url"].endswith("/v1/messages?beta=true") or seen["url"].endswith("/v1/messages")
    assert b["model"] == "claude-sonnet-5-5" and b["stream"] is True
    assert b["system"][0]["cache_control"] == {"type": "ephemeral"}  # prompt + rubric + problem cached
    assert "Compute ∫₀¹ x³ dx" in b["system"][0]["text"]
    img = next(c for c in b["messages"][0]["content"] if c["type"] == "image")
    assert img["source"]["type"] == "base64" and img["source"]["media_type"] == "image/png"
    assert b["output_config"] == {"effort": "low"} and b["fallbacks"] == "default"
    assert "server-side-fallback-2026-07-01" in h["anthropic-beta"]
    assert "temperature" not in b  # sampling params are rejected by current models


def test_anthropic_refusal_raises_so_the_agent_can_fall_back():
    def handler(req: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200, content=anthropic_stream([], stop="refusal"), headers={"content-type": "text/event-stream"}
        )

    client = anthropic.AsyncAnthropic(
        api_key="test", http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    )
    with pytest.raises(CloudRefusal):
        asyncio.run(collect(AnthropicTutor("test", "claude-sonnet-5-5", client).review(ctx())))


def test_openai_responses_request_shape_and_streaming():
    seen: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"], seen["body"] = str(req.url), json.loads(req.content)
        ev = [
            (
                "response.output_text.delta",
                {
                    "type": "response.output_text.delta",
                    "item_id": "m1",
                    "output_index": 0,
                    "content_index": 0,
                    "delta": c,
                    "sequence_number": i,
                    "logprobs": [],
                },
            )
            for i, c in enumerate(ANSWER)
        ]
        return httpx.Response(200, content=sse(ev), headers={"content-type": "text/event-stream"})

    client = openai.AsyncOpenAI(api_key="test", http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    out = asyncio.run(collect(OpenAITutor("test", "gpt-5", client).review(ctx())))
    assert [o["type"] for o in out] == ["speak", "explain"]
    b = seen["body"]
    assert seen["url"].endswith("/responses") and b["model"] == "gpt-5" and b["stream"] is True
    assert "Compute ∫₀¹ x³ dx" in b["instructions"]
    parts = b["input"][0]["content"]
    assert any(p["type"] == "input_image" and p["image_url"].startswith("data:image/png;base64,") for p in parts)


def test_local_openai_compatible_streaming(monkeypatch):
    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        assert body["enable_thinking"] is False and body["model"] == "/models/served"
        chunks = "".join(f"data: {json.dumps({'choices': [{'delta': {'content': c}}]})}\n\n" for c in ANSWER)
        return httpx.Response(200, content=(chunks + "data: [DONE]\n\n").encode())

    out = asyncio.run(
        collect(
            LocalMLX(
                "http://127.0.0.1:8080/v1", "/models/served", "gemma4-26b-a4b", httpx.MockTransport(handler)
            ).review(ctx())
        )
    )
    assert [o["type"] for o in out] == ["speak", "explain"]


def test_privacy_only_tutoring_data_leaves(monkeypatch):
    """What goes to a cloud provider: the problem, the reading, checker verdicts, crops of changed lines — never
    raw strokes (recognition stays local)."""
    seen: dict[str, Any] = {}

    def handler(req: httpx2.Request) -> httpx2.Response:
        seen["body"] = req.content.decode()
        return httpx2.Response(
            200, content=anthropic_stream([ANSWER[0]]), headers={"content-type": "text/event-stream"}
        )

    client = anthropic.AsyncAnthropic(
        api_key="test", http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    )
    asyncio.run(collect(AnthropicTutor("test", "claude-sonnet-5-5", client).review(ctx())))
    assert '"pts"' not in seen["body"] and "pressure" not in seen["body"]
