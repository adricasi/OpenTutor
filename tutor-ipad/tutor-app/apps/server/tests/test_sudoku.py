"""Sudoku mode (Phase 10): grid geometry, the sudoku.valid checker and the marks it owns, on synthetic fixtures.

No real recording of a sudoku exists yet: the fixtures are generated (ink-tutor/tools/synthetic/generate.py)
and the reader is faked with their ground truth. Real handwriting will need its own fixture.
"""

from __future__ import annotations

import subprocess
import sys

import pytest
from fastapi.testclient import TestClient
from inktutor_protocol import ReadingLine
from inktutor_server.agent import agent as agent_mod
from inktutor_server.agent.page_checks import CheckLine, run_page_checks
from inktutor_server.app import create_app
from inktutor_server.checkers.sudoku import (
    check_grid,
    conflicts,
    empty_grid,
    next_move,
    parse_puzzle,
    solve,
    unique_solution,
)
from inktutor_server.config import Settings
from inktutor_server.fixtures import page_strokes_at
from inktutor_server.ink.grid import find_grid, grid_layout, region_cell
from inktutor_server.modes import load_mode
from synthetic import annotation, checkpoint_t, fake_reader, feedback, fixture, run, text_of, truth_lines, visible
from test_agent import FakeRuntime, FakeTutor

CONFLICT, SOLVED = "synthetic-sudoku-conflict", "synthetic-sudoku-solved"
PUZZLE = "53..7....6..195....98....6.8...6...34..8.3..17...2...6.6....28....419..5....8..79"
SOLUTION = "534678912672195348198342567859761423426853791713924856961537284287419635345286179"


# ---------------------------------------------------------------- checker


def test_parse_puzzle_accepts_common_layouts():
    g = parse_puzzle(PUZZLE)
    assert g is not None and g[0][:3] == [5, 3, 0]
    drawn = "\n".join("|".join(PUZZLE[r * 9 + c * 3 : r * 9 + c * 3 + 3] for c in range(3)) for r in range(9))
    assert parse_puzzle("Solve this:\n" + drawn.replace(".", "0")) == g
    assert parse_puzzle("no puzzle here 123") is None


def test_solver_finds_the_unique_solution_and_detects_ambiguity():
    sol = unique_solution(parse_puzzle(PUZZLE))
    assert sol is not None and "".join(str(v) for row in sol for v in row) == SOLUTION
    assert len(solve(empty_grid(), limit=2)) == 2
    assert unique_solution(empty_grid()) is None  # many solutions: never used to judge digits
    hard = parse_puzzle("8..........36......7..9.2...5...7.......457.....1...3...1....68..85...1..9....4..")
    assert unique_solution(hard) is not None


def test_conflicts_name_row_column_and_box():
    g = empty_grid()
    g[0][0] = g[0][5] = 4  # same row
    g[3][1] = g[8][1] = 7  # same column
    g[6][6] = g[7][8] = 2  # same box (bottom-right), different row and column
    found = {(k.cell, k.unit) for k in conflicts(g)}
    assert ((0, 0), "row") in found and ((0, 5), "row") in found
    assert ((3, 1), "column") in found and ((8, 1), "column") in found
    assert ((6, 6), "box") in found and ((7, 8), "box") in found
    assert next(k for k in conflicts(g) if k.unit == "box").where == "box 9 (bottom-right)"


def test_check_grid_statuses():
    g = parse_puzzle(PUZZLE)
    chk = check_grid({(0, 0): 5, (0, 1): 8, (0, 2): 4, (4, 1): 6, (8, 0): 2}, g)
    st = {cell: c.status for cell, c in chk.cells.items()}
    assert st == {(0, 0): "given", (0, 1): "given_mismatch", (0, 2): "ok", (4, 1): "conflict", (8, 0): "wrong"}
    assert chk.cells[(4, 1)].conflicts[0].where == "column 2"
    assert chk.cells[(8, 0)].expected == 3 and 3 in chk.cells[(8, 0)].candidates
    assert not chk.solved and chk.next_move is not None


def test_next_move_finds_naked_and_hidden_singles():
    g = parse_puzzle(SOLUTION)
    g[4][4] = 0
    m = next_move(g)
    assert m is not None and (m.cell, m.digit, m.kind) == ((4, 4), 5, "naked")
    m = next_move(parse_puzzle(PUZZLE))
    sol = parse_puzzle(SOLUTION)
    assert m is not None and sol[m.cell[0]][m.cell[1]] == m.digit  # a real deduction, consistent with the solution


# ---------------------------------------------------------------- geometry on the synthetic fixtures


@pytest.mark.parametrize("stem", [CONFLICT, SOLVED])
def test_grid_is_found_and_digits_land_in_their_cells(stem):
    strokes = page_strokes_at(fixture(stem), 1e12)
    lay = grid_layout(strokes)
    assert lay.grid is not None
    truth = [ln for ln in truth_lines(stem) if ln.get("cell") and ln["text"]]
    cells = {tuple(ln["cell"]): set(ln["strokes"]) for ln in truth}
    got = {region_cell(r): set(r.stroke_ids) for r in lay.regions}
    assert got == cells  # every written digit is one region, in the right cell, with exactly its strokes
    w = lay.grid.xs[-1] - lay.grid.xs[0]
    for a, b in zip(lay.grid.xs, lay.grid.xs[1:], strict=False):
        assert abs((b - a) - w / 9) < 0.2 * w / 9  # boundaries snap to the drawn lines


def test_pencil_marks_and_title_are_not_read():
    lay = grid_layout(page_strokes_at(fixture(CONFLICT), 1e12))
    notes = next(ln for ln in truth_lines(CONFLICT) if ln.get("note") == "pencil marks")
    assert set(lay.notes) == set(notes["strokes"])
    title = next(ln for ln in truth_lines(CONFLICT) if ln["text"] == "SUDOKU")
    assert set(title["strokes"]) <= set(lay.outside)


def test_no_grid_until_enough_lines_are_drawn():
    rec = fixture(CONFLICT)
    strokes = page_strokes_at(rec, 1e12)
    lines = find_grid(strokes).line_ids
    partial = [s for s in strokes if s.id in set(lines[:8])]
    assert find_grid(partial) is None
    assert grid_layout(partial).regions == []


# ---------------------------------------------------------------- page checks (no agent)


def _lines(stem: str, t: float):
    strokes = page_strokes_at(fixture(stem), t)
    lay = grid_layout(strokes)
    truth = truth_lines(stem)
    out = []
    for r in lay.regions:
        text = text_of(truth, r.stroke_ids)
        reading = ReadingLine(id=r.id, stroke_ids=r.stroke_ids, bbox=r.bbox, kind="math", latex=text, confidence=0.9)
        out.append(CheckLine(r, reading, text))
    return lay, out, {s.id: s for s in strokes}


def _cell_line(lines, cell):
    return next(ln for ln in lines if region_cell(ln.region) == cell)


def test_page_check_marks_only_the_student_conflict_and_hints_name_the_column():
    lay, lines, strokes = _lines(CONFLICT, checkpoint_t(CONFLICT, 1))
    res = run_page_checks(load_mode("sudoku"), fixture(CONFLICT).session.problem, lay, lines, strokes)
    six = _cell_line(lines, (4, 1))
    assert [(m.line_id, m.glyph, m.text) for m in res.marks] == [(six.region.id, "cross", "two 6s in column 2")]
    assert res.marks[0].stroke_ids == six.region.stroke_ids  # drawn at the cell, not in a margin
    v = res.verdicts[six.region.id]
    assert v.ok is False and "column 2" in v.hints[1] and "column 2" in v.hints[2] and "row 7" in v.hints[2]
    assert "can't be 6" in v.hints[3] and v.hints[4] == "This cell is 2."
    assert not v.ghost  # never written on the page
    assert res.verdicts[_cell_line(lines, (0, 0)).region.id].ok is True  # a given


def test_solution_mismatch_is_named_on_request_only_unless_configured():
    lay, lines, strokes = _lines(CONFLICT, 1e12)
    problem = fixture(CONFLICT).session.problem
    res = run_page_checks(load_mode("sudoku"), problem, lay, lines, strokes)
    two = _cell_line(lines, (8, 0))
    assert not any(m.line_id == two.region.id for m in res.marks)  # no ✗ unprompted
    v = res.verdicts[two.region.id]
    assert v.ok is None and "doesn't fit the solution" in v.hints[2] and v.hints[4] == "This cell is 3."
    # a fine cell's hints point at the wrong digit first
    four = res.verdicts[_cell_line(lines, (0, 2)).region.id]
    assert four.ok is True and "row 9" in four.hints[1] and "row 9, column 1" in four.hints[2]
    m = load_mode("sudoku")
    eager = m.__class__(**{**m.__dict__, "extra": {**m.extra, "solution_check": "unprompted"}})
    res = run_page_checks(eager, problem, lay, lines, strokes)
    assert any(x.line_id == two.region.id and x.glyph == "cross" for x in res.marks)


def test_without_the_puzzle_only_rules_are_checked():
    lay, lines, strokes = _lines(CONFLICT, 1e12)
    res = run_page_checks(load_mode("sudoku"), "", lay, lines, strokes)
    crossed = {region_cell(next(ln.region for ln in lines if ln.region.id == m.line_id)) for m in res.marks}
    assert crossed == {(4, 1), (6, 1)}  # both 6s in column 2: without the puzzle, neither is known to be a given


def test_solved_grid_gets_one_check():
    lay, lines, strokes = _lines(SOLVED, 1e12)
    res = run_page_checks(load_mode("sudoku"), fixture(SOLVED).session.problem, lay, lines, strokes)
    assert [(m.glyph, m.text) for m in res.marks] == [("check", "solved")]
    last = max(lines, key=lambda ln: max(list(strokes).index(i) for i in ln.region.stroke_ids))
    assert res.marks[0].line_id == last.region.id


# ---------------------------------------------------------------- the agent, end to end (fake reader + tutor)


@pytest.fixture
def sudoku_client(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_mod.Agent, "_read", fake_reader(CONFLICT, "math"))
    m = load_mode("sudoku")
    fast = m.__class__(**{**m.__dict__, "settle_ms": 150})
    monkeypatch.setattr(agent_mod, "load_mode", lambda name: fast)

    def line_at(cell_text):
        return lambda c: next((x.id for x in c.lines if any(cell_text in k for k in x.checks)), "L_none")

    tutor = FakeTutor(
        [
            {"type": "mark", "line_id": line_at("row 9, column 1 = 2"), "glyph": "cross", "text": "tutor says wrong"},
            {"type": "mark", "line_id": line_at("row 1, column 3"), "glyph": "check", "text": "tutor tick"},
            {
                "type": "mark",
                "line_id": line_at("row 5, column 2"),
                "glyph": "cross",
                "text": "tutor on a decided cell",
            },
        ]
    )
    app = create_app(Settings(data_dir=tmp_path, tier1_enabled=False, tier2_provider="none"))
    with TestClient(app) as client:
        app.state.svc.runtime = FakeRuntime(tutor)
        yield client


def _strokes_of_cell(cell):
    return next(set(ln["strokes"]) for ln in truth_lines(CONFLICT) if ln.get("cell") == list(cell) and ln["text"])


def test_replay_marks_the_conflict_and_the_tutor_cannot_grade(sudoku_client):
    fb = feedback(run(sudoku_client, CONFLICT, "sudoku", 1e12))
    marks = [f for f in visible(fb) if f["type"] == "mark"]
    crosses = [f for f in marks if f["glyph"] == "cross"]
    assert len(crosses) == 1 and crosses[0]["source"] == "checker"
    assert set(crosses[0]["anchor"]["stroke_ids"]) == _strokes_of_cell((4, 1))
    assert crosses[0]["text"] == "two 6s in column 2"
    texts = [f.get("text") or "" for f in fb]
    assert "tutor tick" not in texts and "tutor on a decided cell" not in texts  # ✓ is the checker's; decided cell
    q = next(f for f in marks if f["source"] == "tutor")
    assert q["glyph"] == "question" and q["text"] == "tutor says wrong"  # a tutor ✗ becomes a question


def test_hint_ladder_on_the_cross_and_on_a_quiet_wrong_digit(sudoku_client):
    def ask(ws, inbox, page_id, got):
        cross = next(f for f in feedback(got) if f["type"] == "mark" and f["glyph"] == "cross")
        two_line = next(
            m["reading"]["lines"] for m in reversed(got) if m["type"] == "reading" and m["reading"]["lines"]
        )
        two = next(ln["id"] for ln in two_line if set(ln["stroke_ids"]) == _strokes_of_cell((8, 0)))
        out = []
        for lid, fid in ((cross["anchor"]["line_id"], cross["id"]), (two, None)):
            ws.send_json({"type": "hint.request", "page_id": page_id, "line_id": lid, "feedback_id": fid, "level": 3})
            out += inbox.until(lambda m, _: m["type"] == "status" and m["phase"] in ("waiting", "error"), 5)
        return out

    fb = feedback(run(sudoku_client, CONFLICT, "sudoku", 1e12, then=ask))
    hints = [f["text"] for f in fb if f["type"] == "hint"]
    assert hints[0].startswith("This cell can't be 6") and hints[1].startswith("This cell isn't 2")
    assert not any(f["type"] == "ghost" for f in fb)  # never fills a cell


def test_low_confidence_digit_asks_instead_of_grading(sudoku_client, monkeypatch):
    monkeypatch.setattr(agent_mod.Agent, "_read", fake_reader(CONFLICT, "math", {"6": 0.3}))
    fb = visible(feedback(run(sudoku_client, CONFLICT, "sudoku", checkpoint_t(CONFLICT, 1))))
    assert any(f["type"] == "question" and f["text"].startswith("I read this as 6") for f in fb)
    assert not any(f["type"] == "mark" and f["glyph"] == "cross" for f in fb)  # the 6s aren't graded yet


def test_cross_is_retracted_when_the_other_six_is_erased(sudoku_client):
    other = _strokes_of_cell((6, 1))

    def erase(ws, inbox, page_id, got):
        ws.send_json({"type": "ink.update", "page_id": page_id, "strokes_changed": [], "strokes_removed": list(other)})
        return inbox.until(lambda m, out: False, 1.5)

    # no puzzle in the problem: the 6 at r7c2 is just ink, so erasing it ends the conflict
    fb = feedback(run(sudoku_client, CONFLICT, "sudoku", checkpoint_t(CONFLICT, 1), problem="", then=erase))
    crosses = [f for f in fb if f["type"] == "mark" and f["glyph"] == "cross"]
    on_r5c2 = [f for f in crosses if set(f["anchor"]["stroke_ids"]) == _strokes_of_cell((4, 1))]
    assert on_r5c2 and all(f["id"] in {x["refers_to"] for x in fb if x["type"] == "retract"} for f in on_r5c2)


def test_synthetic_fixtures_are_up_to_date():
    gen = Settings().spec_dir / "tools" / "synthetic" / "generate.py"
    r = subprocess.run([sys.executable, str(gen), "--check"], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout
    assert annotation(CONFLICT)["mode"] == "sudoku" and fixture(CONFLICT).mode == "sudoku"
