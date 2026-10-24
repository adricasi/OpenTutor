"""Helpers for the synthetic sudoku / accounting fixtures (ink-tutor/tools/synthetic/generate.py).

The fake reader returns the generator's ground truth for the strokes of a region (what a perfect tier-1 reader
would read), so the agent tests exercise geometry → checkers → marks without any model.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi.testclient import TestClient
from inktutor_protocol import ReadingLine
from inktutor_server.config import Settings
from inktutor_server.fixtures import list_fixtures, load, replay_timed
from test_agent import Inbox


def fixture(stem: str):
    return load(next(p for p in list_fixtures() if p.stem == stem))


def annotation(stem: str) -> dict[str, Any]:
    return json.loads((Settings().annotations_dir / f"{stem}.json").read_text())


def truth_lines(stem: str) -> list[dict[str, Any]]:
    return annotation(stem)["synthetic"]["lines"]


def text_of(lines: list[dict[str, Any]], stroke_ids: list[str]) -> str:
    ids = set(stroke_ids)
    return " ".join(str(ln["text"]) for ln in lines if ids & set(ln["strokes"]) and ln["text"])


def fake_reader(stem: str, kind: str, confidence: dict[str, float] | None = None):
    """Agent._read replacement: the ground-truth text of the region's strokes."""
    lines = truth_lines(stem)

    async def read(self, region, strokes):
        text = text_of(lines, region.stroke_ids)
        return ReadingLine(
            id=region.id,
            stroke_ids=region.stroke_ids,
            bbox=region.bbox,
            kind=kind,
            latex=text,
            text=text,
            confidence=(confidence or {}).get(text, 0.9),
        )

    return read


def run(client: TestClient, stem: str, mode: str, until_ms: float, problem: str | None = None, wait_s=1.5, then=None):
    """Replay a synthetic fixture up to `until_ms` into a session; returns every message received."""
    rec = fixture(stem)
    body = {"mode": mode, "problem": rec.session.problem if problem is None else problem}
    sess = client.post("/api/sessions", json=body).json()
    got: list[dict[str, Any]] = []
    with client.websocket_connect(f"/ws/session/{sess['id']}") as ws:
        inbox = Inbox(ws)
        assert inbox.get(5)["type"] == "session"
        for t, msg in replay_timed(rec):
            if t > until_ms:
                break
            ws.send_json(msg.model_dump(mode="json"))
        got += inbox.until(lambda m, out: False, wait_s)
        if then:
            got += then(ws, inbox, rec.pages[0].id, got)
    return got


def feedback(msgs):
    return [m["feedback"] for m in msgs if m["type"] == "feedback"]


def visible(fb):
    """Feedback still on the page (not retracted)."""
    gone = {f["refers_to"] for f in fb if f["type"] == "retract"}
    return [f for f in fb if f["type"] != "retract" and f["id"] not in gone]


def checkpoint_t(stem: str, i: int) -> float:
    return float(annotation(stem)["checkpoints"][i]["t"])
