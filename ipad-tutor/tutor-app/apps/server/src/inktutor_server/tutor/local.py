"""LocalMLX: the tier-2 model served by mlx-vlm (or LM Studio) on an OpenAI-compatible endpoint."""

from __future__ import annotations

import base64
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx

from .base import (
    ExplainContext,
    HintContext,
    JsonLines,
    LocateContext,
    PlanContext,
    ReviewContext,
    SummaryContext,
    VoiceContext,
    load_prompt,
    summary_prompt,
)

log = logging.getLogger("inktutor.tutor")


def system_prompt(mode: str, kind: str, problem: str, rubric: str) -> str:
    # stable across calls of a session: system prompt + rubric + problem (cacheable); the user turn is the delta
    problem_part = f"# Problem\n{problem.strip() or '(not given)'}"
    if kind == "converse":  # short on purpose: voice answers read only what they need
        return f"{load_prompt(mode, kind)}\n\n{problem_part}"
    return f"{load_prompt(mode, kind)}\n\n# Rubric\n{rubric.strip()}\n\n{problem_part}"


_HISTORY_NOTE = (
    "\nprior_work_erased: what the student wrote earlier and wiped from the page (oldest first). "
    "Context only: never mark it or anchor feedback to it."
)


def review_user(ctx: ReviewContext) -> list[dict[str, Any]]:
    lines = [
        {
            "line_id": ln.id,
            "reads": ln.latex or ln.text,
            "kind": ln.kind,
            "checker": ln.checks or None,
            "changed": ln.id in ctx.changed,
        }
        for ln in ctx.lines
    ]
    state: dict[str, Any] = {
        "lines": lines,
        "graph": ctx.graph,
        "already_on_page": ctx.prior,
        "dismissed": ctx.dismissed,
    }
    if ctx.history:
        state["prior_work_erased"] = ctx.history
    text = json.dumps(state, ensure_ascii=False)
    parts: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": f"Page state:\n{text}{_HISTORY_NOTE if ctx.history else ''}\nImages: the changed lines, in order.",
        }
    ]
    for _, png in ctx.images:
        parts.append(
            {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(png).decode()}}
        )
    return parts


def hint_user(ctx: HintContext) -> list[dict[str, Any]]:
    ln = ctx.line
    text = json.dumps(
        {
            "line_id": ln.id,
            "reads": ln.latex or ln.text,
            "checker": ln.checks or None,
            "level": ctx.level,
            "previous_hints": ctx.previous_hints,
            "tapped_mark": ctx.target_text,
        }
        | ({"prior_work_erased": ctx.history} if ctx.history else {}),
        ensure_ascii=False,
    )
    parts: list[dict[str, Any]] = [
        {"type": "text", "text": f"Hint request:\n{text}{_HISTORY_NOTE if ctx.history else ''}"}
    ]
    if ctx.image:
        parts.append(
            {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(ctx.image).decode()}}
        )
    return parts


def converse_user(ctx: VoiceContext) -> list[dict[str, Any]]:
    # what the student said goes last: the page part is the same across a conversation, so the model server's
    # prefix cache reuses it and only the new words are prefilled
    text = json.dumps(
        {
            "lines": [{"line_id": x.id, "reads": x.latex or x.text, "checker": x.checks or None} for x in ctx.lines],
            "pen_is_on": ctx.pen_line.id if ctx.pen_line else None,
            "student_said": ctx.transcript,
        },
        ensure_ascii=False,
    )
    return [{"type": "text", "text": text}]


def summary_system() -> str:
    return summary_prompt()


def summary_user(ctx: SummaryContext) -> list[dict[str, Any]]:
    """The learning log as JSON (teacher view): no images, no strokes."""
    text = json.dumps(
        {"student": ctx.student, "mode": ctx.mode, "problem": ctx.problem, "log": ctx.log}, ensure_ascii=False
    )
    return [{"type": "text", "text": f"Learning log:\n{text}"}]


# ---------------------------------------------------------------- screen tutor (the Mac screen tutor; not part of this folder)
def screen_system(kind: str, recipe: str = "") -> str:
    """prompts/screen.md `## rules` + `## <kind>`, then the app's recipe: stable per app, so the prefix is cached."""
    base = load_prompt("screen", kind)
    return f"{base}\n\n# App recipe\n{recipe.strip()}" if recipe.strip() else base


def _jpeg_part(jpeg: bytes) -> dict[str, Any]:
    return {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()}}


def plan_user(ctx: PlanContext) -> list[dict[str, Any]]:
    state: dict[str, Any] = {
        "app": ctx.app or "(unknown)",
        "window_title": ctx.window_title,
        "max_steps": ctx.max_steps,
    }
    if ctx.done:
        state["steps_already_done"] = ctx.done
    if ctx.note:
        state["why_replanning"] = ctx.note
    if ctx.image_size:
        state["screenshot_px"] = list(ctx.image_size)
    text = (
        f"Screen state:\n{json.dumps(state, ensure_ascii=False)}\n"
        f"Accessibility elements (role | title | value | path):\n{ctx.ax_summary or '(none: no accessibility data)'}\n"
        f"{'Image: the frontmost window.' if ctx.image else 'No screenshot.'}\n"
        f"Goal: {ctx.goal}"  # the goal goes last: the screen part repeats across a replan, the goal is the delta
    )
    parts: list[dict[str, Any]] = [{"type": "text", "text": text}]
    if ctx.image:
        parts.append(_jpeg_part(ctx.image))
    return parts


def locate_user(ctx: LocateContext) -> list[dict[str, Any]]:
    w, h = ctx.image_size
    ask = (
        f"Question: {ctx.question}"
        if ctx.question
        else f"Find: {ctx.description}\nAnswer in pixels of this {w}×{h} image (origin top-left)."
    )
    text = f"App: {ctx.app}\nWindow: {ctx.window_title}\nImage: {w}×{h} px.\n{ask}"
    return [{"type": "text", "text": text}, _jpeg_part(ctx.image)]


def explain_user(ctx: ExplainContext) -> list[dict[str, Any]]:
    text = json.dumps(
        {
            "goal": ctx.goal,
            "app": ctx.app,
            "window_title": ctx.window_title,
            "steps": ctx.steps,
            "current_step": ctx.step or None,
            "level": ctx.level or None,
            "user_said": ctx.question or None,
        },
        ensure_ascii=False,
    )
    return [{"type": "text", "text": text}]


class OpenAICompatible:
    """Streaming chat completions → JSON objects. Used by LocalMLX (and the OpenAI provider)."""

    name = "local"

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str = "",
        timeout: float = 120,
        max_tokens: int = 600,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout
        self.max_tokens = max_tokens
        self.transport = transport  # injectable for tests
        self.label = model

    async def _stream(self, system: str, user: list[dict[str, Any]], max_tokens: int) -> AsyncIterator[dict[str, Any]]:
        body: dict[str, Any] = {
            "model": self.model,
            "stream": True,
            "temperature": 0.2,
            "max_tokens": max_tokens,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        body.update(self.extra_body())
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        parser = JsonLines()
        url = f"{self.base_url}/chat/completions"
        async with (
            httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client,
            client.stream("POST", url, json=body, headers=headers) as resp,
        ):
            if resp.status_code != 200:
                detail = (await resp.aread())[:300].decode(errors="replace")
                raise RuntimeError(f"{self.name} tutor HTTP {resp.status_code}: {detail}")
            async for line in resp.aiter_lines():
                if not line.startswith("data: ") or line.endswith("[DONE]"):
                    continue
                try:
                    delta = (json.loads(line[6:])["choices"][0].get("delta") or {}).get("content") or ""
                except (json.JSONDecodeError, KeyError, IndexError):
                    continue
                for obj in parser.feed(delta):
                    yield obj
        for obj in parser.close():
            yield obj

    def extra_body(self) -> dict[str, Any]:
        return {"enable_thinking": False}  # mlx-vlm: answer directly (latency)

    def review(self, ctx: ReviewContext) -> AsyncIterator[dict[str, Any]]:
        return self._stream(system_prompt(ctx.mode, "review", ctx.problem, ctx.rubric), review_user(ctx), 300)

    def hint(self, ctx: HintContext) -> AsyncIterator[dict[str, Any]]:
        n = 700 if ctx.level == 4 else 220
        return self._stream(system_prompt(ctx.mode, "hint", ctx.problem, ctx.rubric), hint_user(ctx), n)

    def converse(self, ctx: VoiceContext) -> AsyncIterator[dict[str, Any]]:
        return self._stream(system_prompt(ctx.mode, "converse", ctx.problem, ctx.rubric), converse_user(ctx), 160)

    def summarize(self, ctx: SummaryContext) -> AsyncIterator[dict[str, Any]]:
        return self._stream(summary_system(), summary_user(ctx), 260)

    def plan(self, ctx: PlanContext) -> AsyncIterator[dict[str, Any]]:
        return self._stream(screen_system("plan", ctx.recipe), plan_user(ctx), 1400)

    def locate(self, ctx: LocateContext) -> AsyncIterator[dict[str, Any]]:
        kind = "verify" if ctx.question else "locate"
        return self._stream(screen_system(kind, ctx.recipe), locate_user(ctx), 80)

    def explain(self, ctx: ExplainContext) -> AsyncIterator[dict[str, Any]]:
        n = 400 if ctx.level == 4 else 160
        return self._stream(screen_system("explain", ctx.recipe), explain_user(ctx), n)


class LocalMLX(OpenAICompatible):
    name = "local"

    def __init__(
        self, base_url: str, served_model: str, key: str, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        super().__init__(base_url, served_model, transport=transport)
        self.label = key  # manifest key for the badge, e.g. "gemma4-26b-a4b"

    async def warm(self, ctx: VoiceContext) -> None:
        """Prefill the converse prompt while the student is still talking (no question yet, one token, discarded):
        the model server's prefix cache then holds everything up to their words, and idle weights macOS had
        compressed are paged back in before the real question arrives."""
        body: dict[str, Any] = {
            "model": self.model,
            "stream": False,
            "max_tokens": 1,
            "messages": [
                {"role": "system", "content": system_prompt(ctx.mode, "converse", ctx.problem, ctx.rubric)},
                {"role": "user", "content": converse_user(ctx)},
            ],
            **self.extra_body(),
        }
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        async with httpx.AsyncClient(timeout=30, transport=self.transport) as client:
            await client.post(f"{self.base_url}/chat/completions", json=body, headers=headers)
