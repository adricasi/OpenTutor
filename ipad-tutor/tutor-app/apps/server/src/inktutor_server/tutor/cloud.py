"""Cloud tutors behind the same `Tutor` interface — tier 2 only, per session, behind an explicit toggle.

Only the tutoring step leaves the Mac: crops of the changed lines, the reading, the checker verdicts and the
problem (non-negotiable 3: recognition never goes to the cloud). Keys live in `.env`; the browser never sees them.
Prompts are the same markdown files the local tutor uses; output is the same JSON-lines contract.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any

from ..config import Settings
from .base import (
    ExplainContext,
    HintContext,
    JsonLines,
    LocateContext,
    PlanContext,
    ReviewContext,
    SummaryContext,
    VoiceContext,
)
from .local import converse_user, hint_user, review_user, summary_system, summary_user, system_prompt

log = logging.getLogger("inktutor.tutor")

# Models that accept the server-side refusal fallback (`fallbacks: "default"`, Claude API).
FALLBACK_MODELS = {"claude-sonnet-5-5", "claude-opus-5-5", "claude-opus-5", "claude-fable-5-1"}


class CloudRefusal(RuntimeError):
    """The provider declined; the agent falls back to the local tutor."""


def _image_block_anthropic(url: str) -> dict[str, Any]:
    header, data = url.split(",", 1)
    media = header.removeprefix("data:").split(";", 1)[0] or "image/png"
    return {"type": "image", "source": {"type": "base64", "media_type": media, "data": data}}


def to_anthropic(parts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for p in parts:
        if p["type"] == "text":
            out.append({"type": "text", "text": p["text"]})
        elif p["type"] == "image_url":
            out.append(_image_block_anthropic(p["image_url"]["url"]))
    return out


def to_openai(parts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for p in parts:
        if p["type"] == "text":
            out.append({"type": "input_text", "text": p["text"]})
        elif p["type"] == "image_url":
            out.append({"type": "input_image", "image_url": p["image_url"]["url"]})
    return out


class _LocalOnlyScreen:
    """Screen mode is local-only for now: screenshots never go to a cloud provider. The guide loop
    always asks the local tutor; these exist so a cloud tutor fails loudly instead of silently sending a screen."""

    name: str

    def plan(self, ctx: PlanContext) -> AsyncIterator[dict[str, Any]]:
        raise NotImplementedError(f"{self.name}: screen mode is local-only")

    def locate(self, ctx: LocateContext) -> AsyncIterator[dict[str, Any]]:
        raise NotImplementedError(f"{self.name}: screen mode is local-only")

    def explain(self, ctx: ExplainContext) -> AsyncIterator[dict[str, Any]]:
        raise NotImplementedError(f"{self.name}: screen mode is local-only")


class AnthropicTutor(_LocalOnlyScreen):
    name = "anthropic"

    def __init__(self, api_key: str, model: str, client: Any = None) -> None:
        import anthropic

        self.client = client or anthropic.AsyncAnthropic(api_key=api_key, max_retries=1, timeout=60)
        self.model = self.label = model

    async def _stream(self, system: str, parts: list[dict[str, Any]], max_tokens: int) -> AsyncIterator[dict[str, Any]]:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            # stable per session (prompt + rubric + problem) → cached; the user turn carries only the delta
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": to_anthropic(parts)}],
            "output_config": {"effort": "low"},  # short, latency-sensitive answers
        }
        if self.model in FALLBACK_MODELS:  # a policy decline is re-run server-side on Anthropic's fallback model
            kwargs |= {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}
        parser = JsonLines()
        async with self.client.beta.messages.stream(**kwargs) as stream:
            async for text in stream.text_stream:
                for obj in parser.feed(text):
                    yield obj
            final = await stream.get_final_message()
        if final.stop_reason == "refusal":
            raise CloudRefusal(f"Claude declined ({getattr(final.stop_details, 'category', None)})")
        for obj in parser.close():
            yield obj

    def review(self, ctx: ReviewContext) -> AsyncIterator[dict[str, Any]]:
        return self._stream(system_prompt(ctx.mode, "review", ctx.problem, ctx.rubric), review_user(ctx), 4000)

    def hint(self, ctx: HintContext) -> AsyncIterator[dict[str, Any]]:
        n = 8000 if ctx.level == 4 else 3000
        return self._stream(system_prompt(ctx.mode, "hint", ctx.problem, ctx.rubric), hint_user(ctx), n)

    def converse(self, ctx: VoiceContext) -> AsyncIterator[dict[str, Any]]:
        return self._stream(system_prompt(ctx.mode, "converse", ctx.problem, ctx.rubric), converse_user(ctx), 2000)

    def summarize(self, ctx: SummaryContext) -> AsyncIterator[dict[str, Any]]:
        # only when the session's provider is this cloud and the teacher asked for it explicitly
        return self._stream(summary_system(), summary_user(ctx), 1500)


class OpenAITutor(_LocalOnlyScreen):
    name = "openai"

    def __init__(self, api_key: str, model: str, client: Any = None) -> None:
        import openai

        self.client = client or openai.AsyncOpenAI(api_key=api_key, max_retries=1, timeout=60)
        self.model = self.label = model

    async def _stream(self, system: str, parts: list[dict[str, Any]], max_tokens: int) -> AsyncIterator[dict[str, Any]]:
        parser = JsonLines()
        stream = await self.client.responses.create(
            model=self.model,
            instructions=system,
            max_output_tokens=max_tokens,
            stream=True,
            input=[{"role": "user", "content": to_openai(parts)}],
            reasoning={"effort": "low"},
        )
        async for ev in stream:
            if ev.type == "response.output_text.delta":
                for obj in parser.feed(ev.delta):
                    yield obj
            elif ev.type in ("response.failed", "error"):
                raise RuntimeError(f"OpenAI: {getattr(ev, 'message', None) or ev.type}")
        for obj in parser.close():
            yield obj

    def review(self, ctx: ReviewContext) -> AsyncIterator[dict[str, Any]]:
        return self._stream(system_prompt(ctx.mode, "review", ctx.problem, ctx.rubric), review_user(ctx), 4000)

    def hint(self, ctx: HintContext) -> AsyncIterator[dict[str, Any]]:
        n = 8000 if ctx.level == 4 else 3000
        return self._stream(system_prompt(ctx.mode, "hint", ctx.problem, ctx.rubric), hint_user(ctx), n)

    def converse(self, ctx: VoiceContext) -> AsyncIterator[dict[str, Any]]:
        return self._stream(system_prompt(ctx.mode, "converse", ctx.problem, ctx.rubric), converse_user(ctx), 2000)

    def summarize(self, ctx: SummaryContext) -> AsyncIterator[dict[str, Any]]:
        # only when the session's provider is this cloud and the teacher asked for it explicitly
        return self._stream(summary_system(), summary_user(ctx), 1500)


def make_cloud_tutors(settings: Settings) -> dict[str, Any]:
    """Only providers with a key in .env are offered (the settings panel greys out the others)."""
    out: dict[str, Any] = {}
    if settings.anthropic_api_key:
        out["anthropic"] = AnthropicTutor(settings.anthropic_api_key, settings.anthropic_model)
    if settings.openai_api_key:
        out["openai"] = OpenAITutor(settings.openai_api_key, settings.openai_model)
    return out
