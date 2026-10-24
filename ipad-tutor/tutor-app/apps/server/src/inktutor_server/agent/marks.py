"""Checker verdicts → anchored marks and deterministic hints.

Marks come straight from the checkers (fast, no LLM): ✓ for a line whose steps all hold, ✗ in the margin
plus an underline under the exact strokes of the first wrong step. To find those strokes without a
symbol-level recognizer, the line is cut at its handwritten "=" signs (two short flat strokes stacked
closely), which split the chain into the same segments the checker compared.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import sympy as sp
from inktutor_protocol import BBox, ReadingLine, Stroke

from ..checkers.arithmetic import DivisionCheck, check_long_division
from ..checkers.math import ChainResult, StepCheck, check_chain, check_equations, split_items, split_top
from ..ink.raster import stroke_bbox
from ..ink.regions import char_height


@dataclass
class LineVerdict:
    """What the checkers decided for one line, plus where to put the marks."""

    ok: bool | None  # None: nothing checkable / not complete
    label: str = ""  # ≤ 8 words for the mark
    detail: str = ""  # for the tutor context and the popover
    underline: list[str] = field(default_factory=list)  # stroke ids of the wrong part
    hints: dict[int, str] = field(default_factory=dict)  # deterministic hint ladder (1..4)
    ghost: str = ""  # level-3 corrected step, plain text for ghost ink
    kind: str = ""
    value: str | None = None  # what a correct line evaluates to (last value of a chain, solutions, quotient)
    fix_value: str | None = None  # what a corrected version of a wrong line would evaluate to


def equals_signs(strokes: list[Stroke]) -> list[tuple[float, list[str]]]:
    """(x centre, [stroke ids]) of handwritten '=' signs, left to right."""
    h = char_height(strokes)
    bars = [(s.id, stroke_bbox(s)) for s in strokes]
    bars = [(i, b) for i, b in bars if b.w >= 0.25 * h and b.w <= 1.6 * h and b.h <= 0.3 * h + 2]
    used: set[str] = set()
    out: list[tuple[float, list[str]]] = []
    for i, a in bars:
        if i in used:
            continue
        for j, b in bars:
            if j == i or j in used:
                continue
            x_overlap = min(a.x + a.w, b.x + b.w) - max(a.x, b.x)
            dy = abs((a.y + a.h / 2) - (b.y + b.h / 2))
            if x_overlap >= 0.5 * min(a.w, b.w) and 0.12 * h <= dy <= 0.8 * h and 0.5 <= a.w / max(b.w, 1) <= 2:
                used.update((i, j))
                out.append(((min(a.x, b.x) + max(a.x + a.w, b.x + b.w)) / 2, [i, j]))
                break
    return sorted(out)


def segment_strokes(strokes: list[Stroke], n_segments: int) -> list[list[str]] | None:
    """Split a one-row chain into its n '='-separated segments (stroke ids), or None if the geometry
    doesn't match the reading (then marks fall back to the whole line)."""
    eqs = equals_signs(strokes)
    if len(eqs) != n_segments - 1:
        return None
    eq_ids = {i for _, ids in eqs for i in ids}
    cuts = [x for x, _ in eqs]
    segs: list[list[str]] = [[] for _ in range(n_segments)]
    for s in strokes:
        if s.id in eq_ids:
            continue
        b = stroke_bbox(s)
        cx = b.x + b.w / 2
        k = sum(1 for c in cuts if cx > c)
        segs[k].append(s.id)
    return segs if all(segs) else None


def _plain(e: sp.Expr | str) -> str:
    """Short plain-text maths for ghost ink (x**4/4 → x⁴/4)."""
    sup = str.maketrans("0123456789-", "⁰¹²³⁴⁵⁶⁷⁸⁹⁻")
    s = str(e).replace("**", "^").replace("*", "·")
    out, i = [], 0
    while i < len(s):
        if s[i] == "^":
            j = i + 1
            while j < len(s) and (s[j].isdigit() or s[j] == "-"):
                j += 1
            out.append(s[i + 1 : j].translate(sup))
            i = j
        else:
            out.append(s[i])
            i += 1
    return "".join(out).replace("sqrt", "√")


def verdict_for(line: ReadingLine, strokes: dict[str, Stroke]) -> LineVerdict:
    if line.kind == "long_division" and line.structure:
        return _division(line, strokes)
    if not line.latex:
        return LineVerdict(ok=None)
    items = split_items(line.latex)
    if len(items) > 1 and all("=" in it for it in items):
        return _equations(line, items)
    return _chain(line, strokes)


def _chain(line: ReadingLine, strokes: dict[str, Stroke]) -> LineVerdict:
    r: ChainResult = check_chain(line.latex or "")
    if not r.steps:
        return LineVerdict(ok=None, kind="math.equivalent")
    err = r.first_error
    last = next((sg for sg in reversed(r.segments) if sg.expr is not None), None)
    if err is None:
        return LineVerdict(
            ok=True,
            label="",
            detail="; ".join(s.detail for s in r.steps),
            kind="math.equivalent",
            value=_value(last.expr) if last else None,
        )
    ss = [strokes[i] for i in line.stroke_ids if i in strokes]
    n = len(split_top(" ".join(x.strip() for x in (line.latex or "").splitlines()), "="))
    segs = segment_strokes(ss, n) if len((line.latex or "").splitlines()) <= 1 else None
    underline = segs[err.index] if segs and err.index < len(segs) else []
    v = LineVerdict(
        ok=False,
        detail=err.detail,
        underline=underline,
        kind="math.equivalent",
        fix_value=_value(r.segments[err.index - 1].expr),
    )
    prev, seg = r.segments[err.index - 1], r.segments[err.index]
    if "antiderivative" in err.detail and prev.integrand is not None:
        F = sp.integrate(prev.integrand, prev.var)
        lim = _limits(prev.latex)
        v.label = "check the antiderivative"
        v.hints = {
            1: f"Differentiate {_plain(seg.antiderivative)}: do you get {_plain(prev.integrand)} back?",
            2: "Power rule: the antiderivative of xⁿ is xⁿ⁺¹/(n+1). Then evaluate F(b) − F(a).",
            4: f"Antiderivative {_plain(F)}; evaluated: {_plain(prev.expr)}.",
        }
        v.ghost = f"{_plain(F)} {lim}= {_plain(prev.expr)}".strip()
    else:
        v.label = "this step changes the value"
        v.hints = {
            1: "Compare this step with the one before it: are they really equal?",
            2: f"The previous step is worth {err.expected}; this one isn't.",
            4: f"It should be {err.expected}.",
        }
        v.ghost = f"= {err.expected}" if err.expected else ""
    v.hints[3] = f"Corrected step: {v.ghost}" if v.ghost else v.hints[2]
    return v


def _list(v: object) -> list[object]:
    return list(v) if isinstance(v, list | tuple) else []


def _ids(v: object) -> list[str]:
    return [str(x) for x in _list(v)]


def _value(e: sp.Expr | None) -> str | None:
    """Canonical string of a value, so a fix written elsewhere on the page can be recognized."""
    if e is None:
        return None
    try:
        return str(sp.nsimplify(sp.simplify(e)))
    except Exception:
        return str(e)


def _limits(latex: str) -> str:
    import re

    m = re.search(r"_\s*\{?\s*([^{}^\s]+)\s*\}?\s*\^\s*\{?\s*([^{}\s]+)\s*\}?", latex)
    sub, sup = str.maketrans("0123456789-", "₀₁₂₃₄₅₆₇₈₉₋"), str.maketrans("0123456789-", "⁰¹²³⁴⁵⁶⁷⁸⁹⁻")
    return f"]{m.group(1).translate(sub)}{m.group(2).translate(sup)} " if m else ""


def _equations(line: ReadingLine, items: list[str]) -> LineVerdict:
    steps: list[StepCheck] = check_equations(items)
    if not steps:
        return LineVerdict(ok=None, kind="math.equation")
    err = next((s for s in steps if not s.ok), None)
    if err is None:
        return LineVerdict(
            ok=True,
            detail="each equation keeps the same solution",
            kind="math.equation",
            value=steps[-1].detail.split(" ")[1] if steps else None,
        )
    before, bad = items[err.index - 1], items[err.index]
    try:
        from ..checkers.math import _parse

        e = _parse(before)
        fixed = f"{_plain(sp.simplify(e.lhs))} = {_plain(sp.simplify(e.rhs))}"
        sol = sp.solveset(e.lhs - e.rhs, sorted(e.free_symbols, key=str)[0])
        sol_txt = ", ".join(_plain(x) for x in sol) if isinstance(sol, sp.FiniteSet) else str(sol)
    except Exception:
        fixed, sol_txt = "", ""
    return LineVerdict(
        ok=False,
        label="check this step",
        detail=f"{bad}: {err.detail}",
        kind="math.equation",
        ghost=fixed,
        hints={
            1: "Look at how you simplified from the previous equation to this one.",
            2: "Collect like terms carefully: ax − bx = (a − b)x.",
            3: f"Corrected step: {fixed}" if fixed else "Redo this step from the previous equation.",
            4: f"Solution: {sol_txt}" if sol_txt else "Solve the previous equation step by step.",
        },
    )


def _division(line: ReadingLine, strokes: dict[str, Stroke]) -> LineVerdict:
    st = line.structure or {}
    r: DivisionCheck | None = check_long_division(st)
    if r is None:
        return LineVerdict(ok=None, kind="arithmetic.long_division")
    if r.ok:
        return LineVerdict(
            ok=True if r.complete else None,
            detail=r.detail,
            kind="arithmetic.long_division",
            value=f"{st.get('dividend')}/{st.get('divisor')}={r.expected_quotient}",
        )
    quotient_ids = [i for i in _ids(st.get("quotient_stroke_ids")) if i in strokes]
    rows = [_ids(r) for r in _list(st.get("row_stroke_ids"))]
    digit = r.wrong_digit or 0
    product_row = rows[2 * digit] if 2 * digit < len(rows) else []
    divisor = st.get("divisor", "")
    v = LineVerdict(
        ok=False,
        label="remainder ≥ divisor" if (r.remainder_if_written or 0) >= int(str(divisor) or 1) else "check this digit",
        detail=r.detail,
        underline=product_row or quotient_ids,
        kind="arithmetic.long_division",
    )
    if r.expected_digit is not None and r.partial is not None:
        d, p = int(r.expected_digit), r.partial
        dv = int("".join(c for c in str(divisor) if c.isdigit()) or 1)
        v.ghost = f"{d}: {d}·{dv} = {d * dv}, {p} − {d * dv} = {p - d * dv}"
        v.hints = {
            1: f"Compare what is left ({r.remainder_if_written}) with the divisor ({dv}).",
            2: "What is left after subtracting must be smaller than the divisor; otherwise the digit can go up.",
            3: f"Corrected step: {v.ghost}",
            4: f"{st.get('dividend')} ÷ {dv} = {r.expected_quotient}",
        }
    v.underline = product_row or quotient_ids
    v.hints.setdefault(1, "Check this digit of the quotient.")
    return v


def quotient_anchor(line: ReadingLine) -> list[str]:
    st = line.structure or {}
    return _ids(st.get("quotient_stroke_ids"))


def bbox_of(ids: list[str], strokes: dict[str, Stroke]) -> BBox | None:
    bs = [stroke_bbox(strokes[i]) for i in ids if i in strokes]
    if not bs:
        return None
    x0, y0 = min(b.x for b in bs), min(b.y for b in bs)
    return BBox(x=x0, y=y0, w=max(b.x + b.w for b in bs) - x0, h=max(b.y + b.h for b in bs) - y0)
