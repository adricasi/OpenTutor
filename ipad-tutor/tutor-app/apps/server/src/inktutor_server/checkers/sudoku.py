"""sudoku.valid — deterministic sudoku checks (non-negotiable 4: checkers before any LLM).

- Rules: a digit may appear once per row, column and 3×3 box. Every pair of equal digits in one unit is a
  conflict, reported for both cells (the caller decides which ones to mark: never a given).
- Givens: when the problem carries the puzzle (81 cells, digits and . / 0 / _ for blanks), a written digit
  that differs from the given in that cell is a copying slip.
- Solution: a small backtracking solver (bitmasks, fewest-candidates-first) finds the puzzle's solutions,
  stopping at two. Only a puzzle with exactly one solution is used to judge digits against it.
- Next deducible move: a naked single (a cell with one candidate) or a hidden single (a digit with one place
  left in a row, column or box), so the tutor can explain the next step without filling cells.

Rows, columns and boxes are 0-based in code, 1-based in every text meant for the student.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache

Grid = list[list[int]]  # 9×9, 0 = empty
Cell = tuple[int, int]  # (row, col), 0-based
ALL = 0x3FE  # bits 1..9


def empty_grid() -> Grid:
    return [[0] * 9 for _ in range(9)]


def box_of(r: int, c: int) -> int:
    return (r // 3) * 3 + c // 3


BOX_NAMES = ["top-left", "top", "top-right", "left", "centre", "right", "bottom-left", "bottom", "bottom-right"]


def unit_name(unit: str, index: int) -> str:
    """'row 3', 'column 8', 'box 5 (centre)'."""
    if unit == "box":
        return f"box {index + 1} ({BOX_NAMES[index]})"
    return f"{unit} {index + 1}"


def cell_name(cell: Cell) -> str:
    return f"row {cell[0] + 1}, column {cell[1] + 1}"


def parse_puzzle(text: str) -> Grid | None:
    """The puzzle from a problem statement: 81 cells in reading order, blanks as . 0 _ or *.
    Whitespace and | + - separators (as in a drawn ASCII grid) are ignored."""
    s = re.sub(r"[\s|+\-]", "", text or "")
    m = re.search(r"[0-9._*]{81}", s)
    if not m:
        return None
    vals = [int(ch) if ch.isdigit() else 0 for ch in m.group(0)]
    return [vals[r * 9 : r * 9 + 9] for r in range(9)]


def format_grid(g: Grid) -> str:
    return "\n".join("".join(str(v) if v else "." for v in row) for row in g)


@dataclass(frozen=True)
class Conflict:
    cell: Cell
    other: Cell
    unit: str  # "row" | "column" | "box"
    index: int  # which row / column / box
    digit: int

    @property
    def where(self) -> str:
        return unit_name(self.unit, self.index)


def conflicts(g: Grid) -> list[Conflict]:
    """Every (cell, other) pair with the same digit in one unit, both directions, row → column → box."""
    out: list[Conflict] = []
    for r in range(9):
        for c in range(9):
            d = g[r][c]
            if not d:
                continue
            for c2 in range(9):
                if c2 != c and g[r][c2] == d:
                    out.append(Conflict((r, c), (r, c2), "row", r, d))
            for r2 in range(9):
                if r2 != r and g[r2][c] == d:
                    out.append(Conflict((r, c), (r2, c), "column", c, d))
            b = box_of(r, c)
            for r2 in range(b // 3 * 3, b // 3 * 3 + 3):
                for c2 in range(b % 3 * 3, b % 3 * 3 + 3):
                    if (r2, c2) != (r, c) and r2 != r and c2 != c and g[r2][c2] == d:
                        out.append(Conflict((r, c), (r2, c2), "box", b, d))
    return out


def _masks(g: Grid) -> tuple[list[int], list[int], list[int]]:
    rows, cols, boxes = [0] * 9, [0] * 9, [0] * 9
    for r in range(9):
        for c in range(9):
            if g[r][c]:
                bit = 1 << g[r][c]
                rows[r] |= bit
                cols[c] |= bit
                boxes[box_of(r, c)] |= bit
    return rows, cols, boxes


def candidates(g: Grid, r: int, c: int) -> list[int]:
    """Digits that fit an empty cell given what is already in its row, column and box."""
    rows, cols, boxes = _masks(g)
    free = ALL & ~(rows[r] | cols[c] | boxes[box_of(r, c)])
    return [d for d in range(1, 10) if free >> d & 1]


def solve(g: Grid, limit: int = 2) -> list[Grid]:
    """Up to `limit` solutions of `g` (empty if the grid already breaks a rule or has none)."""
    if conflicts(g):
        return []
    grid = [row[:] for row in g]
    rows, cols, boxes = _masks(grid)
    empties = [(r, c) for r in range(9) for c in range(9) if not grid[r][c]]
    out: list[Grid] = []

    def rec() -> None:
        if len(out) >= limit:
            return
        best: tuple[int, int, int] | None = None  # (count, index, free mask)
        for i, (r, c) in enumerate(empties):
            if grid[r][c]:
                continue
            free = ALL & ~(rows[r] | cols[c] | boxes[box_of(r, c)])
            n = bin(free).count("1")
            if best is None or n < best[0]:
                best = (n, i, free)
                if n <= 1:
                    break
        if best is None:
            out.append([row[:] for row in grid])
            return
        n, i, free = best
        if n == 0:
            return
        r, c = empties[i]
        b = box_of(r, c)
        for d in range(1, 10):
            if not free >> d & 1:
                continue
            bit = 1 << d
            grid[r][c] = d
            rows[r] |= bit
            cols[c] |= bit
            boxes[b] |= bit
            rec()
            grid[r][c] = 0
            rows[r] &= ~bit
            cols[c] &= ~bit
            boxes[b] &= ~bit
            if len(out) >= limit:
                return

    rec()
    return out


@lru_cache(maxsize=32)
def _unique(key: str) -> str | None:
    g = [[int(key[r * 9 + c]) for c in range(9)] for r in range(9)]
    sols = solve(g, limit=2)
    return "".join(str(v) for row in sols[0] for v in row) if len(sols) == 1 else None


def unique_solution(puzzle: Grid) -> Grid | None:
    """The solution if the puzzle has exactly one (cached per puzzle); None if it has none or several."""
    s = _unique("".join(str(v) for row in puzzle for v in row))
    return [[int(s[r * 9 + c]) for c in range(9)] for r in range(9)] if s else None


@dataclass
class Move:
    cell: Cell
    digit: int
    kind: str  # "naked" (the cell has one candidate) | "hidden" (the digit has one place in a unit)
    unit: str = ""  # for hidden singles: "row" | "column" | "box"
    index: int = 0

    @property
    def where(self) -> str:
        return unit_name(self.unit, self.index) if self.kind == "hidden" else unit_name("row", self.cell[0])


def next_move(g: Grid) -> Move | None:
    """The next single a student can deduce from `g` (naked singles first, then hidden singles by unit)."""
    if conflicts(g):
        return None
    for r in range(9):
        for c in range(9):
            if not g[r][c]:
                cand = candidates(g, r, c)
                if len(cand) == 1:
                    return Move((r, c), cand[0], "naked")
    units: list[tuple[str, int, list[Cell]]] = []
    for b in range(9):
        units.append(("box", b, [(b // 3 * 3 + i // 3, b % 3 * 3 + i % 3) for i in range(9)]))
    for r in range(9):
        units.append(("row", r, [(r, c) for c in range(9)]))
    for c in range(9):
        units.append(("column", c, [(r, c) for r in range(9)]))
    for unit, index, cells in units:
        present = {g[r][c] for r, c in cells}
        for d in range(1, 10):
            if d in present:
                continue
            spots = [(r, c) for r, c in cells if not g[r][c] and d in candidates(g, r, c)]
            if len(spots) == 1:
                return Move(spots[0], d, "hidden", unit, index)
    return None


@dataclass
class CellCheck:
    cell: Cell
    digit: int
    status: str  # "given" | "ok" | "conflict" | "given_mismatch" | "wrong" | "unknown"
    conflicts: list[Conflict] = field(default_factory=list)
    expected: int | None = None  # the given, or the solution digit
    candidates: list[int] = field(default_factory=list)  # what fits here, from the trusted digits only


@dataclass
class SudokuCheck:
    cells: dict[Cell, CellCheck]
    puzzle: Grid | None
    solution: Grid | None  # only when the puzzle has exactly one
    grid: Grid  # givens ∪ written digits
    solved: bool
    next_move: Move | None  # deduced from the trusted digits (givens + written digits that are not wrong)
    wrong: list[Cell] = field(default_factory=list)  # written digits that contradict the solution


def check_grid(written: dict[Cell, int], puzzle: Grid | None = None) -> SudokuCheck:
    """Check the digits written on the page against the rules, the givens and (if unique) the solution."""
    puzzle = puzzle if puzzle and not conflicts(puzzle) else None
    solution = unique_solution(puzzle) if puzzle else None
    grid = [row[:] for row in puzzle] if puzzle else empty_grid()
    for (r, c), d in written.items():
        if not (puzzle and puzzle[r][c]):
            grid[r][c] = d
    # rule conflicts on the page as written (a miscopied given counts as what the student wrote)
    page = [row[:] for row in grid]
    for (r, c), d in written.items():
        page[r][c] = d
    by_cell: dict[Cell, list[Conflict]] = {}
    for k in conflicts(page):
        by_cell.setdefault(k.cell, []).append(k)
    cells: dict[Cell, CellCheck] = {}
    wrong: list[Cell] = []
    for (r, c), d in written.items():
        given = puzzle[r][c] if puzzle else 0
        if given and d == given:
            cells[(r, c)] = CellCheck((r, c), d, "given", expected=given)
        elif given:
            cells[(r, c)] = CellCheck((r, c), d, "given_mismatch", expected=given)
        elif by_cell.get((r, c)):
            cells[(r, c)] = CellCheck((r, c), d, "conflict", conflicts=by_cell[(r, c)])
        elif solution:
            ok = solution[r][c] == d
            cells[(r, c)] = CellCheck((r, c), d, "ok" if ok else "wrong", expected=solution[r][c])
        else:
            cells[(r, c)] = CellCheck((r, c), d, "unknown")
        if solution and not given and solution[r][c] != d:
            wrong.append((r, c))
    # what the student can trust: givens + written digits that are neither conflicting nor wrong
    trusted = [row[:] for row in puzzle] if puzzle else empty_grid()
    for cell, chk in cells.items():
        if chk.status in ("ok", "unknown"):
            trusted[cell[0]][cell[1]] = chk.digit
    for cell, chk in cells.items():
        if chk.status in ("conflict", "wrong", "given_mismatch"):
            chk.candidates = candidates(trusted, *cell) if not trusted[cell[0]][cell[1]] else []
    filled = all(page[r][c] for r in range(9) for c in range(9))
    solved = filled and not by_cell and not wrong and all(ch.status != "given_mismatch" for ch in cells.values())
    return SudokuCheck(
        cells=cells,
        puzzle=puzzle,
        solution=solution,
        grid=grid,
        solved=solved,
        next_move=None if solved else next_move(trusted),
        wrong=sorted(wrong),
    )
