"""Baseline for the before/after comparison: the same local model, WITHOUT this app's pipeline.

At each checkpoint of a recording, plain Gemma 4 gets only an image of the page and "Check my work." — no
recognizer reading, no checker verdict, no line ids, no tutor prompts, no hint ladder. Its answer is scored with
the same LLM judge prompt as `make judge` (tests/judge.md) plus three yes/no questions (found the mistake? pointed
at the line? gave the solution away?). Report: .data/judge/baseline-<model>-<time>.json.

    uv run python -m inktutor_server.eval.baseline --model gemma4-26b-a4b
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import io
import json
import time
from pathlib import Path
from typing import Any

import httpx

from ..config import Settings, configure_hf
from ..fixtures import list_fixtures, load, page_strokes_at
from ..ink.raster import rasterize
from ..judge import JUDGE_PROMPT
from ..runtime.manifest import load_manifest
from ..runtime.runtime import Runtime
from ..tutor.base import loads_lenient

STUDENT_PROMPT = "Check my work."
CLASSIFY = (
    "You read a tutor's reply to a student's handwritten maths. The truth is given. Answer with one JSON object "
    'only: {"found_mistake": true|false|null, "located_line": true|false|null, "gave_solution": true|false}. '
    "found_mistake: the reply says the wrong step is wrong (null if the work has no mistake). located_line: it "
    "points at where the mistake is (which line/step) (null if no mistake). gave_solution: it states the final "
    "correct answer or does the remaining work."
)


async def chat(
    c: httpx.AsyncClient, url: str, model: str, messages: list[dict[str, Any]], max_tokens: int
) -> tuple[str, float, float]:
    body = {
        "model": model,
        "stream": True,
        "temperature": 0.2,
        "max_tokens": max_tokens,
        "enable_thinking": False,
        "messages": messages,
    }
    t0, first, out = time.perf_counter(), None, []
    async with c.stream("POST", f"{url}/chat/completions", json=body) as r:
        async for line in r.aiter_lines():
            if not line.startswith("data: ") or line.endswith("[DONE]"):
                continue
            d = (json.loads(line[6:])["choices"][0].get("delta") or {}).get("content") or ""
            if d and first is None:
                first = time.perf_counter() - t0
            out.append(d)
    return "".join(out), first or 0.0, time.perf_counter() - t0


async def main_async(a: argparse.Namespace) -> int:
    s = Settings(local_model=a.model)
    configure_hf(s)
    rt = Runtime(s)
    rt.manifest = load_manifest(s.manifest_path)
    await rt.use_local_model(a.model)
    tutor = rt.tutor("local")
    if tutor is None:
        print("model not ready:", rt.local_error)
        return 2
    print("model:", a.model, rt.local_mode)
    url, served = tutor.base_url, tutor.model
    rec = load(next(p for p in list_fixtures() if p.stem == a.fixture))
    results = []
    problem = rec.session.problem or "Compute the definite integrals and the divisions."
    async with httpx.AsyncClient(timeout=300) as c:
        for cp in rec.checkpoints:
            img = rasterize(page_strokes_at(rec, cp.t), scale=1.0)
            buf = io.BytesIO()
            img.save(buf, "PNG")
            url_img = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
            msgs = [
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": url_img}},
                        {"type": "text", "text": STUDENT_PROMPT},
                    ],
                }
            ]
            reply, ttft, total = await chat(c, url, served, msgs, 500)
            lines = "\n".join(f"- {ln}" for ln in cp.truth.splitlines() if ln.strip()) or "(see image)"
            jmsgs = [
                {"role": "system", "content": JUDGE_PROMPT.read_text()},
                {
                    "role": "user",
                    "content": f"Problem: {problem}\n\n"
                    f"What the student wrote (as read):\n{lines}\n\nEXPECTED:\n{cp.expect}\n\n"
                    f"ACTUAL:\n- the tutor replied (plain text, no marks on the page): {reply}",
                },
            ]
            jtext, _, _ = await chat(c, url, served, jmsgs, 200)
            jobj = next((o for o in (loads_lenient(x) for x in [*jtext.splitlines(), jtext]) if o), {}) or {}
            ctext, _, _ = await chat(
                c,
                url,
                served,
                [
                    {"role": "system", "content": CLASSIFY},
                    {"role": "user", "content": f"Checkpoint kind: {cp.kind}\nTruth:\n{cp.truth}\n\nReply:\n{reply}"},
                ],
                80,
            )
            cobj = next((o for o in (loads_lenient(x) for x in [*ctext.splitlines(), ctext]) if o), {}) or {}
            r = {
                "checkpoint": cp.id,
                "kind": cp.kind,
                "t_ms": cp.t,
                "ttft_s": round(ttft, 2),
                "total_s": round(total, 2),
                "reply": reply,
                "judge_pass": bool(jobj.get("pass")),
                "judge_reason": str(jobj.get("reason", "")),
                **{k: cobj.get(k) for k in ("found_mistake", "located_line", "gave_solution")},
            }
            results.append(r)
            print(
                f"{cp.id} ({cp.kind}): judge {'PASS' if r['judge_pass'] else 'FAIL'} · found={r['found_mistake']} "
                f"located={r['located_line']} gave_solution={r['gave_solution']} · first output {ttft:.1f}s"
            )
    out = Path(s.data_dir) / "judge" / f"baseline-{a.model}-{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {
                "model": a.model,
                "mode": rt.local_mode,
                "fixture": a.fixture,
                "prompt": STUDENT_PROMPT,
                "passed": sum(r["judge_pass"] for r in results),
                "checkpoints": len(results),
                "results": results,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    print("report:", out)
    await rt.stop()
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model", default="gemma4-26b-a4b")
    ap.add_argument("--fixture", default="rec-math-math-22r_69")
    raise SystemExit(asyncio.run(main_async(ap.parse_args())))


if __name__ == "__main__":
    main()
