#!/usr/bin/env python3
"""Synthetic recordings for modes with no real recording yet (sudoku, accounting).

    python3 tools/synthetic/generate.py            # (re)writes fixtures/recordings/synthetic-*.json + annotations
    python3 tools/synthetic/generate.py --check    # exit 1 if the files on disk differ from what it would write

Stdlib only and deterministic (seeded jitter), in the recorder's format (`inktutor.recording/1`, see
docs/RECORDINGS.md): strokes are drawn with a small single-stroke font (Hershey-style polylines, one glyph =
one to three pen strokes, slightly jittered), timed like a pen (pen-up pauses between glyphs, words, lines;
a long pause at each checkpoint). The sidecar annotation holds the ground truth: per checkpoint `truth` and
`expect`, plus `synthetic.lines` = which strokes spell which text (the fake reader in the tests uses it).

These are NOT real recordings: geometry and checkers are tested on them, real handwriting is not. Replace or
complement them with real sessions once sudoku / accounting are recorded.
"""

from __future__ import annotations

import itertools
import json
import math
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # ink-tutor/
REC_DIR = ROOT / "fixtures" / "recordings"
ANN_DIR = ROOT / "fixtures" / "annotations"
W, H = 1194, 628
INK = "#1F2328"

Pt = tuple[float, float]


def _ellipse(cx: float, cy: float, rx: float, ry: float, n: int = 18) -> list[Pt]:
    # start at the top, go anticlockwise (as most people write 0 and O), close the loop
    return [(cx - rx * math.sin(2 * math.pi * i / n), cy - ry * math.cos(2 * math.pi * i / n)) for i in range(n + 1)]


# glyph = strokes; each stroke = points in a box 0.6 wide × 1.0 tall (y down)
FONT: dict[str, list[list[Pt]]] = {
    "0": [_ellipse(0.3, 0.5, 0.27, 0.5)],
    "1": [[(0.12, 0.2), (0.32, 0.0), (0.32, 1.0)]],
    "2": [[(0.04, 0.22), (0.12, 0.06), (0.3, 0.0), (0.48, 0.06), (0.56, 0.22), (0.5, 0.42), (0.02, 1.0), (0.6, 1.0)]],
    "3": [
        [
            (0.04, 0.1),
            (0.25, 0.0),
            (0.46, 0.05),
            (0.54, 0.22),
            (0.44, 0.4),
            (0.24, 0.48),
            (0.46, 0.54),
            (0.58, 0.72),
            (0.5, 0.92),
            (0.28, 1.0),
            (0.04, 0.92),
        ]
    ],
    "4": [[(0.4, 0.0), (0.02, 0.66), (0.6, 0.66)], [(0.46, 0.28), (0.46, 1.0)]],
    "5": [
        [(0.1, 0.02), (0.06, 0.45), (0.28, 0.38), (0.5, 0.46), (0.58, 0.68), (0.5, 0.9), (0.28, 1.0), (0.04, 0.92)],
        [(0.1, 0.02), (0.56, 0.02)],
    ],
    "6": [
        [
            (0.5, 0.04),
            (0.3, 0.0),
            (0.12, 0.14),
            (0.04, 0.5),
            (0.08, 0.84),
            (0.28, 1.0),
            (0.48, 0.94),
            (0.57, 0.74),
            (0.5, 0.56),
            (0.3, 0.5),
            (0.1, 0.58),
            (0.05, 0.72),
        ]
    ],
    "7": [[(0.02, 0.02), (0.58, 0.02), (0.22, 1.0)]],
    "8": [
        [
            (0.3, 0.48),
            (0.1, 0.36),
            (0.08, 0.14),
            (0.3, 0.0),
            (0.5, 0.14),
            (0.48, 0.36),
            (0.3, 0.48),
            (0.06, 0.66),
            (0.08, 0.9),
            (0.3, 1.0),
            (0.52, 0.9),
            (0.54, 0.66),
            (0.3, 0.48),
        ]
    ],
    "9": [
        [
            (0.54, 0.3),
            (0.44, 0.06),
            (0.26, 0.0),
            (0.08, 0.1),
            (0.04, 0.3),
            (0.18, 0.46),
            (0.4, 0.44),
            (0.54, 0.3),
            (0.54, 0.6),
            (0.44, 1.0),
        ]
    ],
    "+": [[(0.3, 0.3), (0.3, 0.8)], [(0.06, 0.55), (0.54, 0.55)]],
    "=": [[(0.06, 0.42), (0.54, 0.42)], [(0.06, 0.68), (0.54, 0.68)]],
    "-": [[(0.08, 0.55), (0.5, 0.55)]],
    ",": [[(0.22, 0.84), (0.14, 1.1)]],
    "/": [[(0.5, 0.0), (0.1, 1.0)]],
    "(": [[(0.4, -0.05), (0.18, 0.3), (0.16, 0.7), (0.4, 1.05)]],
    ")": [[(0.2, -0.05), (0.42, 0.3), (0.44, 0.7), (0.2, 1.05)]],
    "A": [[(0.0, 1.0), (0.3, 0.0), (0.6, 1.0)], [(0.12, 0.62), (0.48, 0.62)]],
    "B": [
        [(0.06, 1.0), (0.06, 0.0)],
        [
            (0.06, 0.0),
            (0.4, 0.02),
            (0.52, 0.14),
            (0.48, 0.38),
            (0.08, 0.48),
            (0.46, 0.54),
            (0.58, 0.74),
            (0.48, 0.96),
            (0.06, 1.0),
        ],
    ],
    "C": [[(0.56, 0.14), (0.4, 0.0), (0.2, 0.04), (0.04, 0.3), (0.04, 0.7), (0.2, 0.96), (0.4, 1.0), (0.58, 0.86)]],
    "D": [[(0.06, 0.0), (0.06, 1.0)], [(0.06, 0.0), (0.34, 0.04), (0.54, 0.3), (0.54, 0.7), (0.34, 0.96), (0.06, 1.0)]],
    "E": [[(0.54, 0.0), (0.06, 0.0), (0.06, 1.0), (0.56, 1.0)], [(0.06, 0.5), (0.44, 0.5)]],
    "F": [[(0.56, 0.0), (0.06, 0.0), (0.06, 1.0)], [(0.06, 0.5), (0.44, 0.5)]],
    "G": [
        [
            (0.56, 0.14),
            (0.4, 0.0),
            (0.2, 0.04),
            (0.04, 0.3),
            (0.04, 0.7),
            (0.2, 0.96),
            (0.42, 1.0),
            (0.58, 0.86),
            (0.58, 0.56),
            (0.34, 0.56),
        ]
    ],
    "H": [[(0.06, 0.0), (0.06, 1.0)], [(0.54, 0.0), (0.54, 1.0)], [(0.06, 0.5), (0.54, 0.5)]],
    "I": [[(0.1, 0.0), (0.5, 0.0)], [(0.3, 0.0), (0.3, 1.0)], [(0.1, 1.0), (0.5, 1.0)]],
    "J": [[(0.5, 0.0), (0.5, 0.78), (0.4, 0.98), (0.2, 1.0), (0.06, 0.84)]],
    "K": [[(0.06, 0.0), (0.06, 1.0)], [(0.54, 0.0), (0.08, 0.56), (0.56, 1.0)]],
    "L": [[(0.06, 0.0), (0.06, 1.0), (0.54, 1.0)]],
    "M": [[(0.02, 1.0), (0.06, 0.0), (0.3, 0.6), (0.54, 0.0), (0.58, 1.0)]],
    "N": [[(0.06, 1.0), (0.06, 0.0), (0.54, 1.0), (0.54, 0.0)]],
    "O": [_ellipse(0.3, 0.5, 0.29, 0.5)],
    "P": [[(0.06, 1.0), (0.06, 0.0)], [(0.06, 0.0), (0.4, 0.02), (0.54, 0.16), (0.5, 0.4), (0.08, 0.5)]],
    "Q": [_ellipse(0.3, 0.5, 0.29, 0.5), [(0.36, 0.72), (0.6, 1.04)]],
    "R": [[(0.06, 1.0), (0.06, 0.0)], [(0.06, 0.0), (0.4, 0.02), (0.54, 0.16), (0.5, 0.4), (0.08, 0.5), (0.56, 1.0)]],
    "S": [
        [
            (0.54, 0.12),
            (0.36, 0.0),
            (0.14, 0.04),
            (0.06, 0.24),
            (0.2, 0.44),
            (0.44, 0.54),
            (0.56, 0.76),
            (0.46, 0.96),
            (0.24, 1.0),
            (0.04, 0.88),
        ]
    ],
    "T": [[(0.05, 0.0), (0.55, 0.0)], [(0.3, 0.0), (0.3, 1.0)]],
    "U": [[(0.06, 0.0), (0.06, 0.74), (0.18, 0.96), (0.32, 1.0), (0.48, 0.92), (0.54, 0.7), (0.54, 0.0)]],
    "V": [[(0.0, 0.0), (0.3, 1.0), (0.6, 0.0)]],
    "W": [[(0.0, 0.0), (0.14, 1.0), (0.3, 0.4), (0.46, 1.0), (0.6, 0.0)]],
    "X": [[(0.04, 0.0), (0.56, 1.0)], [(0.56, 0.0), (0.04, 1.0)]],
    "Y": [[(0.02, 0.0), (0.3, 0.5), (0.58, 0.0)], [(0.3, 0.5), (0.3, 1.0)]],
    "Z": [[(0.04, 0.0), (0.56, 0.0), (0.04, 1.0), (0.58, 1.0)]],
}
ADVANCE, SPACING, SPACE = 0.6, 0.16, 0.9  # × size


def _resample(poly: list[Pt], step: float) -> list[Pt]:
    out = [poly[0]]
    for a, b in itertools.pairwise(poly):
        d = math.dist(a, b)
        n = max(1, round(d / step))
        out += [(a[0] + (b[0] - a[0]) * k / n, a[1] + (b[1] - a[1]) * k / n) for k in range(1, n + 1)]
    return out


class Page:
    """Accumulates strokes and the event log the recorder would have written."""

    def __init__(self, stem: str, seed: int) -> None:
        self.stem = stem
        self.rng = random.Random(seed)
        self.page_id = f"p_syn_{stem}"
        self.t = 1500.0
        self.n = 0
        self.strokes: list[dict[str, object]] = []
        self.events: list[dict[str, object]] = []
        self.lines: list[dict[str, object]] = []  # ground truth: which strokes spell which text
        self.checkpoints: list[dict[str, object]] = []

    def stroke(self, poly: list[Pt], speed: float = 0.3, step: float = 3.0, jitter: float = 0.35) -> str:
        """Draw one pen stroke along `poly` (CSS px); returns its id."""
        self.n += 1
        sid = f"s_syn_{self.stem}_{self.n:04d}"
        pts: list[list[float]] = []
        prev: Pt | None = None
        for i, (x, y) in enumerate(_resample(poly, step)):
            if prev is not None:
                self.t += max(4.0, math.dist(prev, (x, y)) / speed)
            prev = (x, y)
            jx, jy = self.rng.uniform(-jitter, jitter), self.rng.uniform(-jitter, jitter)
            pressure = 0.32 + 0.12 * math.sin(i / 3.0)
            pts.append([round(self.t), round(x + jx, 1), round(y + jy, 1), round(pressure, 3), 38, -6])
        if len(pts) == 1:
            pts.append([pts[0][0] + 8, pts[0][1] + 0.5, pts[0][2] + 0.5, pts[0][3], 38, -6])
            self.t += 8
        self.strokes.append(
            {"id": sid, "page": self.page_id, "color": INK, "width": 3, "pointerType": "pen", "pts": pts}
        )
        self.events.append(
            {
                "t": pts[0][0],
                "type": "stroke.start",
                "id": sid,
                "page": self.page_id,
                "pointerType": "pen",
                "color": INK,
                "width": 3,
                "pt": pts[0],
            }
        )
        rest = pts[1:]
        for k in range(0, len(rest), 6):
            batch = rest[k : k + 6]
            self.events.append({"t": batch[-1][0], "type": "stroke.points", "id": sid, "pts": batch})
        self.events.append({"t": pts[-1][0], "type": "stroke.end", "id": sid, "n": len(pts)})
        return sid

    def pause(self, ms: float) -> None:
        self.t += ms

    def glyph(self, ch: str, x: float, y: float, size: float) -> list[str]:
        ids = []
        s = size * self.rng.uniform(0.96, 1.04)
        dx, dy = self.rng.uniform(-0.8, 0.8), self.rng.uniform(-0.8, 0.8)
        slant = self.rng.uniform(-0.05, 0.08)
        for k, poly in enumerate(FONT[ch]):
            if k:
                self.pause(self.rng.uniform(70, 120))
            ids.append(self.stroke([(x + dx + (px + slant * (1 - py)) * s, y + dy + py * s) for px, py in poly]))
        return ids

    def text(self, text: str, x: float, y: float, size: float = 26.0, label: str | None = None) -> list[str]:
        """Write `text` with its top-left at (x, y); records it as one ground-truth line."""
        ids: list[str] = []
        cx = x
        for ch in text:
            if ch == " ":
                cx += SPACE * size
                self.pause(self.rng.uniform(200, 320))
                continue
            ids += self.glyph(ch, cx, y, size)
            cx += (ADVANCE + SPACING) * size
            self.pause(self.rng.uniform(90, 160))
        self.lines.append({"text": label if label is not None else text, "strokes": ids})
        self.pause(self.rng.uniform(700, 1000))
        return ids

    def line(self, a: Pt, b: Pt, bow: float = 1.2) -> str:
        """A long hand-drawn straight line: slightly bowed, faster than writing."""
        n = max(2, int(math.dist(a, b) / 12))
        nx, ny = -(b[1] - a[1]), b[0] - a[0]
        norm = math.hypot(nx, ny) or 1.0
        poly = [
            (
                a[0] + (b[0] - a[0]) * k / n + nx / norm * bow * math.sin(math.pi * k / n),
                a[1] + (b[1] - a[1]) * k / n + ny / norm * bow * math.sin(math.pi * k / n),
            )
            for k in range(n + 1)
        ]
        sid = self.stroke(poly, speed=1.1, step=6.0, jitter=0.3)
        self.pause(self.rng.uniform(250, 400))
        return sid

    def checkpoint(self, kind: str, truth: str, expect: str, note: str = "") -> None:
        self.pause(2500)
        cid = f"cp_syn_{len(self.checkpoints) + 1}"
        self.events.append({"t": round(self.t), "type": "checkpoint", "id": cid, "kind": kind, "page": self.page_id})
        self.checkpoints.append(
            {
                "id": cid,
                "t": round(self.t),
                "page": self.page_id,
                "strokeIds": [str(s["id"]) for s in self.strokes],
                "kind": kind,
                "truth": truth,
                "expect": expect,
                "note": note,
                "draftedBy": "generator",
                "confirmed": False,
            }
        )
        self.pause(2500)

    def files(self, title: str, mode: str, problem: str, intent: str, note: str) -> tuple[dict[str, object], ...]:
        end = round(self.t + 500)
        sid = f"sess_syn_{self.stem}"
        events: list[dict[str, object]] = [
            {"t": 0, "type": "session.start", "mode": mode, "title": title},
            {"t": 0, "type": "page.new", "page": self.page_id, "index": 0, "width": W, "height": H},
            *self.events,
            {"t": end, "type": "session.end"},
        ]
        rec = {
            "schema": "inktutor.recording/1",
            "exportedAt": "2026-10-24T12:00:00Z",
            "session": {
                "id": sid,
                "title": title,
                "mode": mode,
                "problem": problem,
                "intent": intent,
                "startedAt": "2026-10-24T12:00:00Z",
                "endedAt": "2026-10-24T12:10:00Z",
                "durationMs": end,
                "device": {
                    "dpr": 2,
                    "canvasWidth": W,
                    "canvasHeight": H,
                    "maxTouchPoints": 5,
                    "pointerTypesSeen": ["pen"],
                },
            },
            "pages": [{"id": self.page_id, "index": 0, "width": W, "height": H, "dpr": 2, "strokes": self.strokes}],
            "events": events,
            "checkpoints": [
                {k: v for k, v in cp.items() if k in ("id", "t", "page", "strokeIds", "kind")}
                for cp in self.checkpoints
            ],
            "photos": [],
            "stats": {
                "strokes": len(self.strokes),
                "points": sum(len(s["pts"]) for s in self.strokes),  # type: ignore[arg-type]
                "checkpoints": len(self.checkpoints),
                "photos": 0,
                "pages": 1,
            },
        }
        ann = {
            "recording": f"synthetic-{self.stem}.json",
            "mode": mode,
            "problem": problem,
            "note": note,
            "checkpoints": self.checkpoints,
            "photos": {},
            "synthetic": {"generator": "ink-tutor/tools/synthetic/generate.py", "lines": self.lines},
        }
        return rec, ann


# ---------------------------------------------------------------- sudoku

PUZZLE = [
    "53..7....",
    "6..195...",
    ".98....6.",
    "8...6...3",
    "4..8.3..1",
    "7...2...6",
    ".6....28.",
    "...419..5",
    "....8..79",
]
SOLUTION = [
    "534678912",
    "672195348",
    "198342567",
    "859761423",
    "426853791",
    "713924856",
    "961537284",
    "287419635",
    "345286179",
]
SUDOKU_PROBLEM = "Solve the sudoku (copy the grid first). Puzzle:\n" + "\n".join(PUZZLE)


def _digit(p: Page, d: str, r: int, c: int, xs: list[float], ys: list[float]) -> None:
    cw, ch = xs[c + 1] - xs[c], ys[r + 1] - ys[r]
    size = 0.56 * ch
    x = xs[c] + (cw - ADVANCE * size) / 2 + p.rng.uniform(-2, 2)
    y = ys[r] + (ch - size) / 2 + p.rng.uniform(-2, 2)
    ids = p.glyph(d, x, y, size)
    p.lines.append({"text": d, "strokes": ids, "cell": [r, c]})
    p.pause(p.rng.uniform(500, 900))


def _ink_grid(cells: dict[tuple[int, int], str]) -> str:
    return "\n".join("".join(cells.get((r, c), ".") for c in range(9)) for r in range(9))


def sudoku_conflict() -> tuple[dict[str, object], ...]:
    p = Page("sudoku-conflict", seed=11)
    x0, y0, cell = 372.0, 92.0, 50.0
    xs = [x0 + k * cell for k in range(10)]
    ys = [y0 + k * cell for k in range(10)]
    p.text("SUDOKU", 60, 30, 24)
    for k in range(10):  # horizontals, box lines drawn twice (thicker)
        p.line((xs[0] - 3, ys[k] + p.rng.uniform(-1, 1)), (xs[9] + 3, ys[k] + p.rng.uniform(-1, 1)))
        if k % 3 == 0:
            p.line((xs[0] - 2, ys[k] + 2), (xs[9] + 2, ys[k] + 2))
    for k in range(10):
        p.line((xs[k] + p.rng.uniform(-1, 1), ys[0] - 3), (xs[k] + p.rng.uniform(-1, 1), ys[9] + 3))
        if k % 3 == 0:
            p.line((xs[k] + 2, ys[0] - 2), (xs[k] + 2, ys[9] + 2))
    ink: dict[tuple[int, int], str] = {}
    for r, row in enumerate(PUZZLE):
        for c, ch in enumerate(row):
            if ch != ".":
                _digit(p, ch, r, c, xs, ys)
                ink[(r, c)] = ch
    # pencil-mark candidates in r1c9 (small, top-left corner): notes, not an answer
    n1 = p.glyph("1", xs[8] + 5, ys[0] + 4, 11)
    n2 = p.glyph("2", xs[8] + 17, ys[0] + 4, 11)
    p.lines.append({"text": "", "strokes": n1 + n2, "cell": [0, 8], "note": "pencil marks"})
    p.pause(1200)
    for r, c in ((0, 2), (0, 3)):
        _digit(p, SOLUTION[r][c], r, c, xs, ys)
        ink[(r, c)] = SOLUTION[r][c]
    p.checkpoint(
        "step",
        _ink_grid(ink),
        "No marks: the givens are copied correctly, r1c3 = 4 and r1c4 = 6 are right, the small 1 2 in r1c9 are "
        "pencil marks (not read). Asked for a hint: the next deducible move (a naked or hidden single), never a "
        "digit written on the page.",
    )
    _digit(p, "6", 4, 1, xs, ys)  # r5c2 = 6: column 2 already has the given 6 at r7c2
    ink[(4, 1)] = "6"
    p.checkpoint(
        "mistake",
        _ink_grid(ink),
        "✗ on the 6 in row 5, column 2 (only there: the other 6 is a given). Hint level 1 names column 2; level 2 "
        "says column 2 already has a 6 (row 7); never fills the cell.",
    )
    _digit(p, "2", 8, 0, xs, ys)  # r9c1 = 2: no rule broken, but the solution has 3
    ink[(8, 0)] = "2"
    p.checkpoint(
        "final",
        _ink_grid(ink),
        "No new ✗: the 2 in row 9, column 1 breaks no rule, it only contradicts the unique solution (3), which "
        "is said only when the student asks: a hint on that cell says the 2 doesn't fit. The ✗ on r5c2 stays.",
    )
    return p.files(
        "Sudoku: a conflict and a wrong digit",
        "sudoku",
        SUDOKU_PROBLEM,
        "Copy the classic puzzle, fill two right cells, then a 6 that clashes in its column and a 2 that only "
        "contradicts the solution.",
        "Synthetic (generated by ink-tutor/tools/synthetic/generate.py, not a real recording). Grid drawn as 10 + "
        "10 single strokes with the 3×3 box lines doubled; 30 givens; pencil marks in r1c9. truth = the digits "
        "on the page, 9 rows ('.' = empty).",
    )


def sudoku_solved() -> tuple[dict[str, object], ...]:
    p = Page("sudoku-solved", seed=23)
    x0, y0, cell = 400.0, 104.0, 44.0
    xs = [x0 + k * cell for k in range(10)]
    ys = [y0 + k * cell for k in range(10)]
    # border: one closed stroke, clockwise from the top-left corner
    p.stroke(
        _resample([(xs[0], ys[0]), (xs[9], ys[0] + 1), (xs[9] + 1, ys[9]), (xs[0] - 1, ys[9] + 1), (xs[0], ys[0])], 8),
        speed=1.0,
        step=6.0,
    )
    p.pause(400)
    for k in range(1, 9):  # inner lines in two pieces, overshooting the border, slightly slanted
        mid = xs[0] + p.rng.uniform(4.2, 5.0) * cell
        p.line((xs[0] - 6, ys[k] - 1.5), (mid - 3, ys[k]))
        p.line((mid + 3, ys[k]), (xs[9] + 6, ys[k] + 1.5))
        if k % 3 == 0:
            p.line((xs[0] - 2, ys[k] + 2), (xs[9] + 2, ys[k] + 2.5))
    for k in range(1, 9):
        mid = ys[0] + p.rng.uniform(4.2, 5.0) * cell
        p.line((xs[k] + 1.5, ys[0] - 6), (xs[k], mid - 3))
        p.line((xs[k], mid + 3), (xs[k] - 1.5, ys[9] + 6))
        if k % 3 == 0:
            p.line((xs[k] + 2, ys[0] - 2), (xs[k] + 2.5, ys[9] + 2))
    ink: dict[tuple[int, int], str] = {}
    for r, row in enumerate(PUZZLE):
        for c, ch in enumerate(row):
            if ch != ".":
                _digit(p, ch, r, c, xs, ys)
                ink[(r, c)] = ch
    p.checkpoint("step", _ink_grid(ink), "The grid is found (border box, pieces, doubled lines); no marks.")
    for r in range(9):
        for c in range(9):
            if (r, c) not in ink:
                _digit(p, SOLUTION[r][c], r, c, xs, ys)
                ink[(r, c)] = SOLUTION[r][c]
    p.checkpoint("final", _ink_grid(ink), "✓ solved on the last digit written; no ✗ anywhere.")
    return p.files(
        "Sudoku: solved, grid drawn in pieces",
        "sudoku",
        SUDOKU_PROBLEM,
        "Draw the grid roughly (border in one go, inner lines in two pieces), copy the givens, solve it.",
        "Synthetic (generated by ink-tutor/tools/synthetic/generate.py, not a real recording). Geometry stress: the "
        "border is one closed stroke, inner lines are drawn in two pieces that overshoot the border, box lines "
        "doubled. truth = the digits on the page, 9 rows.",
    )


# ---------------------------------------------------------------- accounting


def accounting_journal() -> tuple[dict[str, object], ...]:
    p = Page("accounting-journal", seed=37)
    p.text("DR CASH 1,200", 60, 50)
    p.text("CR SALES 1,200", 100, 100)
    p.checkpoint(
        "step",
        "DR CASH 1,200\nCR SALES 1,200",
        "✓ beside the entry (debits 1,200 = credits 1,200). Nothing else.",
    )
    p.text("DR RENT 300", 60, 180)
    p.text("DR UTILITIES 150", 60, 230)
    p.text("CR CASH 400", 100, 280)
    p.checkpoint(
        "mistake",
        "DR CASH 1,200\nCR SALES 1,200\nDR RENT 300\nDR UTILITIES 150\nCR CASH 400",
        "✗ on the last line of the second entry: debits 450 ≠ credits 400. Hint level 1: add up each side; level "
        "2: debits 450, credits 400; never the corrected amount unprompted. The first entry keeps its ✓.",
    )
    a = p.text("EQUIPMENT", 60, 370, label="")
    b = p.text("800", 420, 370, label="")
    p.lines.pop()
    p.lines[-1] = {"text": "EQUIPMENT 800", "strokes": a + b}
    c = p.text("CASH", 110, 420, label="")
    d = p.text("800", 540, 420, label="")
    p.lines.pop()
    p.lines[-1] = {"text": "CASH 800", "strokes": c + d}
    p.text("1200 + 450 + 800 = 2350", 60, 510)
    p.checkpoint(
        "final",
        "DR CASH 1,200\nCR SALES 1,200\nDR RENT 300\nDR UTILITIES 150\nCR CASH 400\nEQUIPMENT 800\nCASH 800\n"
        "1200 + 450 + 800 = 2350",
        "✓ on the two-column entry (Equipment debit 800 in the left amount column, Cash credit 800 indented in the "
        "right column). ✗ and underline on '2350' in the sum line (1200 + 450 + 800 = 2450).",
    )
    return p.files(
        "Accounting: journal entries",
        "accounting",
        "Record in the journal: (1) cash sales of 1,200; (2) rent 300 and utilities 150 paid in cash; "
        "(3) equipment bought for 800 cash. Then add up the debits.",
        "One balanced entry, one compound entry that doesn't balance, one two-column entry, a wrong sum.",
        "Synthetic (generated by ink-tutor/tools/synthetic/generate.py, not a real recording). Upper-case "
        "single-stroke font. truth = one line per handwritten row.",
    )


def accounting_taccounts() -> tuple[dict[str, object], ...]:
    p = Page("accounting-taccounts", seed=41)
    p.text("DR CASH 500", 60, 40)
    p.text("CR REVENUE 500", 100, 88)
    p.checkpoint("step", "DR CASH 500\nCR REVENUE 500", "✓ beside the journal entry (500 = 500).")

    def t_account(title: str, x0: float, x1: float, y: float = 232.0, bottom: float = 470.0) -> float:
        stem = (x0 + x1) / 2
        p.text(title, stem - len(title) * 10, y - 46, label=title)
        p.line((x0, y), (x1, y + 1))
        p.line((stem, y - 3), (stem + 1, bottom))
        return stem

    s1 = t_account("CASH", 50, 370)
    p.text("500", s1 - 120, 250)
    p.text("200", s1 - 120, 294)
    p.text("250", s1 + 40, 250)
    p.line((60, 334), (360, 335))
    p.text("BAL 450", 62, 346)
    p.checkpoint(
        "step",
        "DR CASH 500\nCR REVENUE 500\nT CASH: debit 500, 200 | credit 250 | rule | debit BAL 450",
        "✓ on BAL 450 (700 − 250 = 450, a debit balance on the debit side).",
    )
    s2 = t_account("REVENUE", 430, 750)
    p.text("500", s2 + 40, 250)
    p.text("300", s2 + 40, 294)
    p.line((s2 + 12, 334), (740, 334))
    p.text("700", s2 + 40, 346)
    s3 = t_account("SUPPLIES", 810, 1130)
    p.text("300", s3 - 120, 250)
    p.line((820, 334), (1120, 335))
    p.text("BAL 300", s3 + 22, 346)
    p.checkpoint(
        "mistake",
        "DR CASH 500\nCR REVENUE 500\nT CASH: debit 500, 200 | credit 250 | rule | debit BAL 450\n"
        "T REVENUE: credit 500, 300 | rule (credit) | credit 700\n"
        "T SUPPLIES: debit 300 | rule | credit BAL 300",
        "✗ on the Revenue total 700 (500 + 300 = 800). ✗ on the Supplies balance: 300 is a debit balance, written "
        "on the credit side. The ✓ on Cash's BAL 450 and on the journal entry stay.",
    )
    return p.files(
        "Accounting: T-accounts",
        "accounting",
        "Post the cash sale of 500 and balance the Cash, Revenue and Supplies T-accounts "
        "(Cash: +500, +200, −250; Revenue: 500 and 300; Supplies bought for 300).",
        "A journal entry, then three T-accounts: a right balance, a wrong total, a balance on the wrong side.",
        "Synthetic (generated by ink-tutor/tools/synthetic/generate.py, not a real recording). truth: the journal "
        "rows, then one line per T-account (rows per side, rules, totals/balances).",
    )


BUILDERS = {
    "sudoku-conflict": sudoku_conflict,
    "sudoku-solved": sudoku_solved,
    "accounting-journal": accounting_journal,
    "accounting-taccounts": accounting_taccounts,
}


def render() -> dict[Path, str]:
    out: dict[Path, str] = {}
    for stem, build in BUILDERS.items():
        rec, ann = build()
        out[REC_DIR / f"synthetic-{stem}.json"] = json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n"
        out[ANN_DIR / f"synthetic-{stem}.json"] = json.dumps(ann, ensure_ascii=False, indent=1) + "\n"
    return out


def main(argv: list[str]) -> int:
    files = render()
    if "--check" in argv:
        stale = [p for p, s in files.items() if not p.exists() or p.read_text() != s]
        for p in stale:
            print(f"out of date: {p.relative_to(ROOT)}")
        return 1 if stale else 0
    for p, s in files.items():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(s)
        print(f"wrote {p.relative_to(ROOT)} ({len(s) // 1024} KiB)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
