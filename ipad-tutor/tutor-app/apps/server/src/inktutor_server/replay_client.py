"""Replay a recording into a running server, as the iPad would, and record what the tutor does.

    uv run python -m inktutor_server.replay_client rec-math-math-22r_69 --speed 3 --until 90 --hints 1,2,3
    uv run python -m inktutor_server.replay_client rec-math-math-22r_69 --checkpoints   # stop at each checkpoint

The judge (`make judge`) uses `run_checkpoints`: one session per recording, paused at every checkpoint
until the tutor is quiet, the page snapshotted, and the hint ladder walked on each new ✗.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
import websockets

from .fixtures import list_fixtures, load, replay_timed


@dataclass
class Snapshot:
    label: str
    t_ms: float
    lines: list[dict[str, Any]]  # the latest reading's lines
    feedback: list[dict[str, Any]]  # visible on the page at this moment
    hints: list[dict[str, Any]] = field(default_factory=list)  # hint/ghost events from walking the ladder
    first_mark_ms: float | None = None  # latency of the first mark after the update that caused it


@dataclass
class Collected:
    readings: list[dict[str, Any]] = field(default_factory=list)
    feedback: list[dict[str, Any]] = field(default_factory=list)
    statuses: list[dict[str, Any]] = field(default_factory=list)
    first_mark_ms: list[float] = field(default_factory=list)
    snapshots: list[Snapshot] = field(default_factory=list)
    log: list[str] = field(default_factory=list)
    tier2: dict[str, Any] = field(default_factory=dict)

    def visible(self) -> list[dict[str, Any]]:
        gone = {f["refers_to"] for f in self.feedback if f["type"] == "retract"}
        return [f for f in self.feedback if f["type"] != "retract" and f["id"] not in gone]


class Client:
    def __init__(self, quiet: bool) -> None:
        self.quiet = quiet
        self.out = Collected()
        self.t0 = time.perf_counter()
        self.last_event = time.perf_counter()
        self.busy = False  # the server is recognizing / checking / reviewing
        self.ws: Any = None

    def say(self, s: str) -> None:
        line = f"[{time.perf_counter() - self.t0:6.1f}s] {s}"
        self.out.log.append(line)
        if not self.quiet:
            print(line, flush=True)

    async def receiver(self) -> None:
        async for raw in self.ws:
            m = json.loads(raw)
            self.last_event = time.perf_counter()
            if m["type"] == "reading":
                self.out.readings.append(m["reading"])
                for ln in m["reading"]["lines"]:
                    self.say(f"reading {ln['id'][-6:]} conf={ln['confidence']:.2f} {(ln.get('latex') or '')[:90]}")
            elif m["type"] == "feedback":
                f = m["feedback"]
                self.out.feedback.append(f)
                if f["type"] == "mark" and m.get("ms"):
                    self.out.first_mark_ms.append(m["ms"])
                lid = f["anchor"].get("line_id") or "?"
                extra = f" → {f['refers_to']}" if f.get("refers_to") else ""
                self.say(
                    f"{f['type']:8} {f.get('glyph') or '':9} line {lid[-6:]} [{f.get('source')}/{m.get('model')} "
                    f"{m.get('ms', 0):.0f} ms] {f.get('text') or ''}{extra}"
                )
            if m["type"] == "status":
                self.busy = m["phase"] in ("received", "recognizing", "checking", "reviewing")
            if m["type"] == "status" and m["phase"] in ("reviewing", "error"):
                self.out.statuses.append(m)
                self.say(f"status   {m['phase']} {m.get('detail', '')}")
            elif m["type"] == "session":
                self.out.tier2 = t2 = m["tier2"]
                self.say(f"session  tutor={t2['provider']}·{t2['model']} ready={t2['ready']} {t2.get('detail', '')}")

    async def quiet_for(self, seconds: float, budget: float) -> None:
        start = time.perf_counter()
        while (self.busy or time.perf_counter() - self.last_event < seconds) and time.perf_counter() - start < budget:
            await asyncio.sleep(0.3)

    async def hint_ladder(self, cross: dict[str, Any], levels: list[int]) -> list[dict[str, Any]]:
        got: list[dict[str, Any]] = []
        for level in levels:
            n0 = len(self.out.feedback)
            self.say(f"→ hint level {level} on {cross['anchor'].get('line_id', '')[-6:]}")
            await self.ws.send(
                json.dumps(
                    {
                        "type": "hint.request",
                        "page_id": cross["page_id"],
                        "feedback_id": cross["id"],
                        "line_id": cross["anchor"].get("line_id"),
                        "level": level,
                        "explicit": level == 4,
                    }
                )
            )
            t1 = time.perf_counter()
            while time.perf_counter() - t1 < 90:
                await asyncio.sleep(0.3)
                if any(f["type"] == "hint" and f.get("hint_level") == level for f in self.out.feedback[n0:]):
                    await asyncio.sleep(0.5)
                    break
            got += [f for f in self.out.feedback[n0:] if f["type"] in ("hint", "ghost")]
        return got


async def run_checkpoints(
    stem: str,
    base: str = "http://localhost:8000",
    speed: float = 4.0,
    stops: list[tuple[float, str]] | None = None,
    hint_levels: list[int] | None = None,
    quiet_s: float = 10.0,
    quiet: bool = False,
    mode: str | None = None,
) -> Collected:
    """Replay `stem`, pausing at each (t_ms, label) stop (default: its checkpoints, else the end)."""
    rec = load(next(p for p in list_fixtures() if p.stem == stem))
    if stops is None:
        stops = [(cp.t, cp.id) for cp in rec.checkpoints] or [(rec.session.duration_ms, "end")]
    stops = sorted(stops)
    async with httpx.AsyncClient(base_url=base, timeout=10) as c:
        body = {"mode": mode or rec.mode, "problem": rec.session.problem}
        sess = (await c.post("/api/sessions", json=body)).json()
    cl = Client(quiet)
    msgs = list(replay_timed(rec))
    async with websockets.connect(base.replace("http", "ws") + f"/ws/session/{sess['id']}", max_size=2**24) as ws:
        cl.ws = ws
        rt = asyncio.create_task(cl.receiver())
        await asyncio.sleep(0.3)
        i = 0
        for t_stop, label in stops:
            start = time.perf_counter()
            t_first = msgs[i][0] if i < len(msgs) else t_stop
            n_marks = len(cl.out.first_mark_ms)
            while i < len(msgs) and msgs[i][0] <= t_stop:
                t, msg = msgs[i]
                delay = (t - t_first) / 1000 / speed - (time.perf_counter() - start) if speed > 0 else 0
                if delay > 0:
                    await asyncio.sleep(min(delay, 3.0))  # don't sit through long pauses (wipes, thinking)
                await ws.send(msg.model_dump_json())
                i += 1
            await cl.quiet_for(quiet_s, budget=150)
            seen = {f["id"] for s in cl.out.snapshots for f in s.feedback}
            snap = Snapshot(
                label=label,
                t_ms=t_stop,
                lines=cl.out.readings[-1]["lines"] if cl.out.readings else [],
                feedback=list(cl.out.visible()),
                first_mark_ms=cl.out.first_mark_ms[n_marks] if len(cl.out.first_mark_ms) > n_marks else None,
            )
            new_crosses = [
                f for f in snap.feedback if f["type"] == "mark" and f.get("glyph") == "cross" and f["id"] not in seen
            ]
            for cross in new_crosses[:2]:
                snap.hints += await cl.hint_ladder(cross, hint_levels or [])
            cl.say(f"── stop {label}: {len(snap.feedback)} on page, {len(snap.hints)} hint events")
            cl.out.snapshots.append(snap)
        rt.cancel()
    return cl.out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("stem")
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--speed", type=float, default=3.0)
    ap.add_argument("--until", type=float, default=None, help="stop once, this many seconds into the recording")
    ap.add_argument("--checkpoints", action="store_true", help="stop at every checkpoint")
    ap.add_argument("--hints", default="", help="hint levels to request on each new ✗, e.g. 1,2,3")
    a = ap.parse_args()
    hints = [int(x) for x in a.hints.split(",") if x.strip()]
    stops = None if a.checkpoints else [((a.until or 1e9) * 1000, "stop")]
    res = asyncio.run(run_checkpoints(a.stem, a.base, a.speed, stops, hints))
    if res.first_mark_ms:
        print(f"mark latencies (ms after the triggering ink.update): {[round(x) for x in res.first_mark_ms[:8]]}")


if __name__ == "__main__":
    main()
