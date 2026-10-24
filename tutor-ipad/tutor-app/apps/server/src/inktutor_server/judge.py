"""Fixture judge: how well does a local tier-2 model tutor the real recordings?

    make judge MODEL=gemma4-26b-a4b           (or: uv run python -m inktutor_server.judge --model qwen36-35b-a3b)

Starts a server with LOCAL_MODEL=<model>, replays every recording that has checkpoints (pausing at each),
walks the hint ladder on each new ✗, then:
- deterministic checks per checkpoint: `mistake` → a ✗ and an underline; `step`/`final` → a ✓ and no ✗;
  first-mark latency;
- an LLM judge (the same local model by default; prompt in apps/server/tests/judge.md) compares what the
  tutor did with the recorded `expect` and answers pass/fail with a reason.
Report: printed table + .data/judge/<model>-<time>.json, so models can be compared on the same fixtures.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import httpx

from .config import APP_ROOT, Settings
from .fixtures import list_fixtures, load
from .replay_client import Snapshot, run_checkpoints
from .tutor.base import loads_lenient

JUDGE_PROMPT = APP_ROOT / "apps" / "server" / "tests" / "judge.md"
GLYPH = {"check": "✓", "cross": "✗", "question": "?", "underline": "underline", "pin": "pin", "circle": "circle"}


def describe(snap: Snapshot) -> tuple[str, str]:
    lines = "\n".join(
        f"- line {ln['id']}: {ln.get('latex') or ln.get('text') or ln.get('structure')}" for ln in snap.lines
    )
    fb = []
    for f in snap.feedback:
        what = GLYPH.get(f.get("glyph") or "", f["type"])
        fb.append(
            f"- {f['type']} {what} on line {f['anchor'].get('line_id')} (from {f.get('source')}): {f.get('text') or ''}"
        )
    for h in snap.hints:
        fb.append(f"- after the student asked: {h['type']} level {h.get('hint_level')}: {h.get('text')}")
    return lines or "(nothing read)", "\n".join(fb) or "(nothing)"


def deterministic(kind: str, snap: Snapshot) -> dict[str, Any]:
    glyphs = [f.get("glyph") for f in snap.feedback if f["type"] == "mark"]
    if kind == "mistake":
        ok = "cross" in glyphs and "underline" in glyphs
        why = "✗ and underline present" if ok else f"marks: {glyphs}"
    elif kind in ("step", "final"):
        ok = "check" in glyphs or "pin" in glyphs or "question" in glyphs
        ok = ok and ("cross" not in glyphs or kind == "final")
        why = f"marks: {glyphs}"
    else:
        ok, why = True, "not checked"
    level4 = any(h.get("hint_level") == 4 for h in snap.hints)
    return {"ok": ok and not level4, "why": why + (" · level-4 hint given unasked" if level4 else "")}


async def judge_one(base_url: str, model: str, problem: str, expect: str, snap: Snapshot) -> dict[str, Any]:
    lines, actual = describe(snap)
    user = (
        f"Problem: {problem}\n\nWhat the student wrote (as read):\n{lines}\n\nEXPECTED:\n{expect}\n\nACTUAL:\n{actual}"
    )
    body = {
        "model": model,
        "temperature": 0,
        "max_tokens": 200,
        "enable_thinking": False,
        "messages": [{"role": "system", "content": JUDGE_PROMPT.read_text()}, {"role": "user", "content": user}],
    }
    async with httpx.AsyncClient(timeout=180) as c:
        r = await c.post(f"{base_url}/chat/completions", json=body)
        text = r.json()["choices"][0]["message"]["content"]
    obj = next((o for o in (loads_lenient(x) for x in [*text.splitlines(), text]) if o), None)
    if not obj:
        return {"pass": False, "reason": f"judge answered no JSON: {text[:120]}"}
    return {"pass": bool(obj.get("pass")), "reason": str(obj.get("reason", ""))}


def start_server(model: str, port: int, data_dir: str) -> subprocess.Popen[bytes]:
    env = {**os.environ, "LOCAL_MODEL": model, "TUTOR_PROVIDER": "local", "DATA_DIR": data_dir}
    log = open(Path(data_dir) / "server.log", "wb")  # noqa: SIM115 — lives as long as the server
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "inktutor_server.app:create_app",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        env=env,
        stdout=log,
        stderr=log,
        start_new_session=True,
        cwd=str(APP_ROOT),
    )


async def wait_ready(base: str, timeout: float = 400) -> dict[str, Any]:
    deadline = time.time() + timeout
    async with httpx.AsyncClient(timeout=3) as c:
        while time.time() < deadline:
            try:
                s = (await c.get(f"{base}/api/settings")).json()
                if not s.get("loading") and (s.get("local_mode") or s.get("local_error")):
                    return s  # type: ignore[no-any-return]
            except (httpx.HTTPError, ValueError):
                pass
            await asyncio.sleep(2)
    raise RuntimeError("server did not get ready")


async def main_async(a: argparse.Namespace) -> int:
    settings = Settings()
    out_dir = settings.data_dir / "judge"
    out_dir.mkdir(parents=True, exist_ok=True)
    data_dir = tempfile.mkdtemp(prefix="judge-")
    base = f"http://127.0.0.1:{a.port}"
    proc = start_server(a.model, a.port, data_dir)
    try:
        s = await wait_ready(base)
        if s.get("local_error") or not s.get("tier1", {}).get("ready"):
            print(f"cannot judge: {s.get('local_error') or s.get('tier1')}")
            return 2
        print(f"tutor: {a.model} ({s['local_mode']}) · tier 1 ready · {s['memory_available_gb']} GiB free\n")
        model_url = settings.local_openai_base_url.rstrip("/")
        async with httpx.AsyncClient(timeout=5) as c:
            served = (await c.get(model_url.rsplit("/v1", 1)[0] + "/health")).json()["loaded_model"]
        results: list[dict[str, Any]] = []
        stems = [p.stem for p in list_fixtures() if not a.fixtures or p.stem in a.fixtures.split(",")]
        for stem in stems:
            rec = load(next(p for p in list_fixtures() if p.stem == stem))
            if not rec.checkpoints:
                continue
            col = await run_checkpoints(stem, base, a.speed, hint_levels=[1, 2, 3], quiet=not a.verbose)
            by_label = {s.label: s for s in col.snapshots}
            for cp in rec.checkpoints:
                snap = by_label.get(cp.id)
                if snap is None:
                    continue
                det = deterministic(cp.kind, snap)
                verdict = (
                    await judge_one(model_url, served, rec.session.problem, cp.expect, snap)
                    if cp.expect
                    else {"pass": None, "reason": "no expect"}
                )
                row = {
                    "fixture": stem,
                    "checkpoint": cp.id,
                    "kind": cp.kind,
                    "t_s": round(cp.t / 1000, 1),
                    "deterministic": det,
                    "judge": verdict,
                    "first_mark_ms": snap.first_mark_ms,
                    "drafted": cp.drafted_by == "auto" and not cp.confirmed,
                    "page": describe(snap)[1],
                }
                results.append(row)
                mark = "PASS" if verdict["pass"] else ("FAIL" if verdict["pass"] is False else " -- ")
                lat = f"{snap.first_mark_ms / 1000:.1f}s" if snap.first_mark_ms else "  -  "
                print(
                    f"{mark}  det={'ok ' if det['ok'] else 'NO '} {lat:>6}  {stem[-18:]:18} {cp.kind:8} "
                    f"t={cp.t / 1000:5.1f}s  {verdict['reason'][:90]}"
                )
        judged = [r for r in results if r["judge"]["pass"] is not None]
        rate = sum(r["judge"]["pass"] for r in judged) / len(judged) if judged else 0.0
        det_rate = sum(r["deterministic"]["ok"] for r in results) / len(results) if results else 0.0
        lats = [r["first_mark_ms"] for r in results if r["first_mark_ms"]]
        summary = {
            "model": a.model,
            "mode": s["local_mode"],
            "judge_pass_rate": round(rate, 3),
            "deterministic_rate": round(det_rate, 3),
            "checkpoints": len(results),
            "first_mark_ms_median": sorted(lats)[len(lats) // 2] if lats else None,
            "results": results,
        }
        path = out_dir / f"{a.model}-{time.strftime('%Y%m%d-%H%M%S')}.json"
        path.write_text(json.dumps(summary, indent=2, ensure_ascii=False))
        print(
            f"\njudge pass rate {rate:.0%} ({len(judged)} judged) · deterministic {det_rate:.0%} · "
            f"median first mark {summary['first_mark_ms_median'] or 0:.0f} ms · report {path.relative_to(APP_ROOT)}"
        )
        return 0
    finally:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=30)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=Settings().local_model)
    ap.add_argument("--port", type=int, default=8020)
    ap.add_argument("--speed", type=float, default=4.0)
    ap.add_argument("--fixtures", default="", help="comma-separated stems (default: all with checkpoints)")
    ap.add_argument("--verbose", action="store_true")
    raise SystemExit(asyncio.run(main_async(ap.parse_args())))


if __name__ == "__main__":
    main()
