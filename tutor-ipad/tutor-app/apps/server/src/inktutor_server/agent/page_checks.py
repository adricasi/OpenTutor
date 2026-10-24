"""Page-level checkers: modes whose verdicts depend on the whole page, not on one line at a time.

- sudoku (recognizer "grid", checker "sudoku.valid"): every written cell is a line; a digit is wrong because
  of other cells (same row, column or box), the givens, or the unique solution.
- accounting (recognizer "ledger", checkers "ledger.balanced", "ledger.taccount", "math.equivalent"): a
  journal line is right or wrong as part of its entry; a T-account total or balance depends on the rows above.

The agent hands over the settled, confident lines; this module returns a verdict per line (so the tutor
never marks what a checker decided, and the hint ladder has deterministic wording) and the marks the
checkers own. The agent reconciles those marks with what is already on the page (new ones placed, ones that
no longer hold retracted).

Digits that contradict the sudoku's unique solution without breaking a rule are not marked unprompted
(mode option solution_check = "on_request", the default): they are named in that cell's hints, and the next-
move hints on any other cell point at them first. With solution_check = "unprompted" they get a ✗ too.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal

from inktutor_protocol import Glyph, ReadingLine, Stroke

from ..checkers.accounting import (
    JournalLine,
    TRow,
    check_journal,
    check_t_account,
    fmt,
    parse_amount,
)
from ..checkers.sudoku import (
    Cell,
    SudokuCheck,
    cell_name,
    check_grid,
    format_grid,
    parse_puzzle,
    unit_name,
)
from ..ink.grid import GridLayout, grid_layout, region_cell
from ..ink.ledger import LedgerLayout, after_rule, ledger_layout
from ..ink.raster import stroke_bbox
from ..ink.regions import Region
from ..ink.words import words
from ..modes import ModeConfig
from .marks import LineVerdict, verdict_for

PAGE_CHECKERS = frozenset({"sudoku.valid", "ledger.balanced", "ledger.taccount"})
PAGE_RECOGNIZERS = frozenset({"grid", "ledger"})
Severity = Literal["ok", "minor", "major"]


@dataclass
class CheckLine:
    """A settled, confident line: its region, its reading and the text to check (the confirmed one, if any)."""

    region: Region
    reading: ReadingLine
    text: str


@dataclass
class PageMark:
    line_id: str
    glyph: Glyph
    severity: Severity
    text: str
    stroke_ids: list[str] = field(default_factory=list)  # empty: the mark sits in the line's margin

    @property
    def key(self) -> str:
        return f"{self.line_id}|{self.glyph}|{self.text}"


@dataclass
class PageCheck:
    verdicts: dict[str, LineVerdict] = field(default_factory=dict)
    marks: list[PageMark] = field(default_factory=list)


def page_layout(recognizer: str, strokes: list[Stroke]) -> GridLayout | LedgerLayout:
    return grid_layout(strokes) if recognizer == "grid" else ledger_layout(strokes)


def uses_page_checks(mode: ModeConfig) -> bool:
    return bool(PAGE_CHECKERS & set(mode.checkers))


def run_page_checks(
    mode: ModeConfig,
    problem: str,
    layout: GridLayout | LedgerLayout | None,
    lines: list[CheckLine],
    strokes: dict[str, Stroke],
) -> PageCheck:
    out = PageCheck()
    if "sudoku.valid" in mode.checkers:
        _sudoku(mode, problem, lines, strokes, out)
    if isinstance(layout, LedgerLayout):
        _ledger(mode, layout, lines, strokes, out)
    return out


# ---------------------------------------------------------------- sudoku


def _digit(text: str) -> int | None:
    t = text.strip().strip("$").strip()
    return int(t) if len(t) == 1 and t in "123456789" else None


def _cands(cs: list[int]) -> str:
    return ", ".join(str(d) for d in cs) if cs else "nothing (an earlier digit must be wrong)"


def _sudoku(mode: ModeConfig, problem: str, lines: list[CheckLine], strokes: dict[str, Stroke], out: PageCheck) -> None:
    order = {sid: i for i, sid in enumerate(strokes)}
    cell_line: dict[Cell, CheckLine] = {}
    written: dict[Cell, int] = {}
    for ln in lines:
        cell = region_cell(ln.region)
        if cell is None:
            continue
        d = _digit(ln.text)
        if d is None:
            out.verdicts[ln.region.id] = LineVerdict(ok=None, detail=f"{cell_name(cell)}: not read as one digit")
            continue
        cell_line[cell] = ln
        written[cell] = d
    if not written:
        return
    chk = check_grid(written, parse_puzzle(problem))
    unprompted = str(mode.extra.get("solution_check", "on_request")) == "unprompted"
    move_hints = _move_hints(chk)
    for cell, cc in chk.cells.items():
        ln = cell_line[cell]
        lid, name, d = ln.region.id, cell_name(cell), cc.digit
        ids = ln.region.stroke_ids
        if cc.status == "given":
            out.verdicts[lid] = LineVerdict(
                ok=True, detail=f"{name} = {d}: a given of the puzzle", hints=dict(move_hints), kind="sudoku.valid"
            )
        elif cc.status == "given_mismatch":
            v = LineVerdict(
                ok=False,
                label=f"the puzzle has {cc.expected} here",
                detail=f"{name} = {d}, but the puzzle gives {cc.expected} in this cell",
                hints={
                    1: "Compare this cell with the puzzle you copied.",
                    2: f"The puzzle gives {cc.expected} in {name}.",
                    3: f"The puzzle gives {cc.expected} in {name}.",
                    4: f"The puzzle gives {cc.expected} in {name}.",
                },
                kind="sudoku.valid",
            )
            out.verdicts[lid] = v
            out.marks.append(PageMark(lid, "cross", "major", v.label, ids))
        elif cc.status == "conflict":
            k = cc.conflicts[0]
            where = k.where
            v = LineVerdict(
                ok=False,
                label=f"two {d}s in {where}",
                detail="; ".join(f"{x.where} already has a {d} at {cell_name(x.other)}" for x in cc.conflicts),
                hints={
                    1: f"Look along {where}: is any digit there twice?",
                    2: f"{where[0].upper() + where[1:]} already has a {d} ({cell_name(k.other)}).",
                    3: f"This cell can't be {d}. What fits here: {_cands(cc.candidates)}.",
                    4: f"This cell is {chk.solution[cell[0]][cell[1]]}."
                    if chk.solution
                    else f"This cell can't be {d}. What fits here: {_cands(cc.candidates)}.",
                },
                kind="sudoku.valid",
            )
            out.verdicts[lid] = v
            out.marks.append(PageMark(lid, "cross", "major", v.label, ids))
        elif cc.status == "wrong":
            v = LineVerdict(
                ok=False if unprompted else None,
                label="doesn't fit the solution" if unprompted else "",
                detail=f"{name} = {d}: no row, column or box conflict, but the puzzle's unique solution has a "
                "different digit here (say so only when the student asks)",
                hints={
                    1: f"Check this {d} again: which digits can still go in {name}?",
                    2: f"This {d} doesn't fit the solution. What fits here: {_cands(cc.candidates)}.",
                    3: f"This cell isn't {d}: try {_cands(cc.candidates)}.",
                    4: f"This cell is {cc.expected}.",
                },
                kind="sudoku.valid",
            )
            out.verdicts[lid] = v
            if unprompted:
                out.marks.append(PageMark(lid, "cross", "major", v.label, ids))
        else:
            known = cc.status == "ok"
            out.verdicts[lid] = LineVerdict(
                ok=True if known else None,
                detail=f"{name} = {d}: " + ("fits the solution" if known else "no row, column or box conflict"),
                hints=dict(move_hints),
                kind="sudoku.valid",
            )
    if chk.solved:
        last = max(cell_line.values(), key=lambda x: max(order.get(i, -1) for i in x.region.stroke_ids))
        out.marks.append(PageMark(last.region.id, "check", "ok", "solved"))


def _move_hints(chk: SudokuCheck) -> dict[int, str]:
    """The hint ladder for a cell that is fine: the next deducible move (or the wrong digit to fix first)."""
    sol = chk.solution
    full = f"Solution:\n{format_grid(sol)}" if sol else "This puzzle has no unique solution to show."
    if chk.solved:
        done = "Solved: every row, column and box has 1–9 once."
        return {1: done, 2: done, 3: done, 4: full}
    if chk.wrong and sol:
        quiet = [w for w in chk.wrong if chk.cells[w].status == "wrong"]  # not already ✗ as a conflict
        r, c = (quiet or chk.wrong)[0]
        d = chk.grid[r][c] or next(cc.digit for cc in chk.cells.values() if cc.cell == (r, c))
        return {
            1: f"Before the next step: one of your digits doesn't fit. Check {unit_name('row', r)} again.",
            2: f"The {d} in {cell_name((r, c))} doesn't fit the solution.",
            3: f"The {d} in {cell_name((r, c))} doesn't fit: erase it and look again.",
            4: f"{cell_name((r, c))[0].upper() + cell_name((r, c))[1:]} is {sol[r][c]}. {full}",
        }
    m = chk.next_move
    if m is None:
        return {
            1: "No single is left: look for two cells in one unit that share the same two candidates.",
            2: "Write the candidates of the cells in one box, then look for a pair or a digit with one place.",
            3: "Find two cells in one unit with the same two candidates: no other cell there can take those digits.",
            4: full,
        }
    where, cell = m.where, cell_name(m.cell)
    if m.kind == "naked":
        return {
            1: f"Look at {where}: one empty cell there has only one possible digit.",
            2: f"Look at {cell}: its row, column and box already use eight different digits.",
            3: f"{cell[0].upper() + cell[1:]} must be {m.digit}: it's the only digit left for it.",
            4: full,
        }
    return {
        1: f"Look at {where}: where can the {m.digit} go?",
        2: f"In {where}, the {m.digit} fits in only one cell.",
        3: f"In {where}, the {m.digit} can only go in {cell}.",
        4: full,
    }


# ---------------------------------------------------------------- accounting


def _amount_x(region: Region, strokes: dict[str, Stroke]) -> float:
    """Centre of the right-most ink word of the line (where the amount is written)."""
    ss = [strokes[i] for i in region.stroke_ids if i in strokes]
    ws = words(ss)
    if not ws:
        return region.bbox.x + region.bbox.w
    bs = [stroke_bbox(strokes[i]) for i in ws[-1]]
    x0, x1 = min(b.x for b in bs), max(b.x + b.w for b in bs)
    return (x0 + x1) / 2


_NUM = re.compile(r"\d[\d.,]*\d|\d")


def arithmetic_latex(text: str) -> str:
    """'1,200 + 450 = 1,650' → '1200 + 450 = 1650' (amounts as plain numbers; × and x between numbers → ·)."""

    def num(m: re.Match[str]) -> str:
        v = parse_amount(m.group(0))
        return m.group(0) if v is None else format(v.normalize(), "f")

    s = _NUM.sub(num, text.replace("$", "").replace("€", ""))
    s = re.sub(r"(?<=\d)\s*[x×]\s*(?=\d)", r" \\cdot ", s)
    return s.replace("÷", "/")


def _ledger(
    mode: ModeConfig, lay: LedgerLayout, lines: list[CheckLine], strokes: dict[str, Stroke], out: PageCheck
) -> None:
    journal = sorted((ln for ln in lines if ln.region.kind == "line"), key=lambda x: (x.region.bbox.y, x.region.bbox.x))
    by_id = {ln.region.id: ln for ln in lines}
    if "ledger.balanced" in mode.checkers and journal:
        jl = [
            JournalLine(
                ln.region.id,
                ln.text,
                ln.region.bbox.x,
                _amount_x(ln.region, strokes),
                lay.h,
                after_rule(ln.region, lay.journal_rules, lay.h),
            )
            for ln in journal
        ]
        jc = check_journal(jl)
        for e in jc.entries:
            summary = f"entry: debits {fmt(e.debits)}, credits {fmt(e.credits)}"
            for p in e.postings:
                out.verdicts[p.line_id] = LineVerdict(
                    ok=None,
                    detail=f"{p.side} {p.account} {fmt(p.amount)}; {summary}"
                    + ("; debits = credits" if e.ok else "")
                    + ("" if e.ok is not None else " (entry not finished)"),
                    kind="ledger.balanced",
                )
            if e.ok:
                out.marks.append(PageMark(e.last, "check", "ok", "debits = credits"))
            elif e.ok is False:
                v = _entry_verdict(e.debits, e.credits, e.complete, summary)
                out.verdicts[e.last] = v
                out.marks.append(PageMark(e.last, "cross", "major", v.label))
        for tot in jc.totals:
            exp = " and ".join(fmt(x) for x in tot.expected)
            v = LineVerdict(
                ok=tot.ok,
                label="" if tot.ok else "check the total",
                detail=tot.detail
                + ("" if tot.ok else f"; written {' and '.join(fmt(x) for x in tot.written) or 'nothing'}"),
                hints={
                    1: "Add up each column above this line again.",
                    2: tot.detail[0].upper() + tot.detail[1:] + ".",
                    3: f"Totals: {exp}.",
                    4: f"Totals: {exp}.",
                },
                kind="ledger.balanced",
            )
            out.verdicts[tot.line_id] = v
            out.marks.append(
                PageMark(tot.line_id, "check" if tot.ok else "cross", "ok" if tot.ok else "major", v.label)
            )
        if "math.equivalent" in mode.checkers:
            for lid in jc.arithmetic:
                ln = by_id[lid]
                v = verdict_for(ln.reading.model_copy(update={"latex": arithmetic_latex(ln.text)}), strokes)
                if v.ok is None:
                    continue
                out.verdicts[lid] = v
                if v.ok:
                    out.marks.append(PageMark(lid, "check", "ok", v.label))
                else:
                    out.marks.append(PageMark(lid, "cross", "major", v.label))
                    if v.underline:
                        out.marks.append(PageMark(lid, "underline", "major", v.label, v.underline))
    if "ledger.taccount" in mode.checkers:
        for t in lay.taccounts:
            title = next(
                (ln for ln in lines if ln.region.kind == "t_title" and ln.region.parts.get("t") == [t.id]), None
            )
            name = title.text.strip() if title else ""
            rows = [
                TRow(ln.region.id, "debit" if ln.region.parts["side"] == ["debit"] else "credit", ln.text, _after(ln))
                for ln in lines
                if ln.region.kind == "t_row" and ln.region.parts.get("t") == [t.id]
            ]
            tc = check_t_account(name, rows)
            side, bal = tc.balance
            summary = f"T-account {tc.name}: debits {fmt(tc.debits)}, credits {fmt(tc.credits)}" + (
                f", {side} balance {fmt(bal)}" if side else ", no balance"
            )
            if title:
                out.verdicts[title.region.id] = LineVerdict(ok=None, detail=summary, kind="ledger.taccount")
            for lid, (s, a) in tc.postings.items():
                out.verdicts[lid] = LineVerdict(ok=None, detail=f"{s} {fmt(a)}; {summary}", kind="ledger.taccount")
            for f in tc.findings:
                v = LineVerdict(ok=f.ok, label=f.label, detail=f.detail, hints=f.hints, kind="ledger.taccount")
                out.verdicts[f.line_id] = v
                out.marks.append(PageMark(f.line_id, "check" if f.ok else "cross", "ok" if f.ok else "major", f.label))


def _after(ln: CheckLine) -> int:
    v = ln.region.parts.get("after") or ["0"]
    return int(v[0])


def _entry_verdict(d: Decimal, c: Decimal, complete: bool, summary: str) -> LineVerdict:
    if complete:
        short = "credits" if c < d else "debits"
        return LineVerdict(
            ok=False,
            label="debits ≠ credits",
            detail=summary,
            hints={
                1: "Add up the debits and the credits of this entry: do they match?",
                2: f"Debits add up to {fmt(d)}, credits to {fmt(c)}.",
                3: f"The {short} are {fmt(abs(d - c))} short: check the amounts of this entry.",
                4: f"Debits {fmt(d)}, credits {fmt(c)}: a difference of {fmt(abs(d - c))}.",
            },
            kind="ledger.balanced",
        )
    missing = "credit" if d > 0 else "debit"
    return LineVerdict(
        ok=False,
        label=f"no {missing} side",
        detail=f"{summary}; the entry has no {missing}",
        hints={
            1: "Every entry needs at least one debit and one credit.",
            2: f"This entry only has {'debits' if d > 0 else 'credits'}: which account takes the other side?",
            3: f"Add a {missing} of {fmt(d or c)} to the account on the other side.",
        },
        kind="ledger.balanced",
    )
