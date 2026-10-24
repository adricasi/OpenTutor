"""math.equivalent — deterministic step checking with sympy (non-negotiable 4: checkers before any LLM).

A handwritten line like  ∫₂⁴ x²+3 dx = [x³/3 + 3x]₂⁴ = 4³/3 + 3·4 − (2³/3 + 3·2) = □  is a chain of
segments joined by "=". Consecutive segments must be equal. The first pair that is not is the mistake;
everything after it is judged against the student's own previous step, so a consequence of an earlier
error is not marked twice. Lines separated by ";" (or new lines that don't start with "=") are separate
equations; consecutive equations must have the same solution set.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import sympy as sp
from sympy.parsing.latex import parse_latex

X = sp.Symbol("x")
UNFINISHED = re.compile(r"^\s*(\\square|\\Box|\?|)\s*$")


@dataclass
class Segment:
    latex: str
    expr: sp.Expr | None = None
    kind: str = "expr"  # expr | integral | evaluation | unfinished | unparsed
    antiderivative: sp.Expr | None = None  # for evaluation brackets: F in [F]_a^b
    integrand: sp.Expr | None = None  # for integrals
    var: sp.Symbol = X


@dataclass
class StepCheck:
    ok: bool
    index: int  # segment index of the right-hand side that failed (or last checked)
    detail: str
    expected: str | None = None
    kind: str = "math.equivalent"


@dataclass
class ChainResult:
    segments: list[Segment]
    steps: list[StepCheck] = field(default_factory=list)
    complete: bool = True  # False when the line ends with "=" or an empty box

    @property
    def first_error(self) -> StepCheck | None:
        return next((s for s in self.steps if not s.ok), None)


KNOWN_COMMANDS = {
    "frac",
    "dfrac",
    "tfrac",
    "sqrt",
    "int",
    "cdot",
    "times",
    "div",
    "left",
    "right",
    "Big",
    "big",
    "Bigg",
    "pi",
    "infty",
    "sin",
    "cos",
    "tan",
    "ln",
    "log",
    "exp",
    "theta",
    "alpha",
    "beta",
    "lambda",
    "mu",
    "square",
    "Box",
    "mathrm",
    "operatorname",
    "le",
    "ge",
    "leq",
    "geq",
    "neq",
}


def _unknown_commands(s: str) -> set[str]:
    return {c for c in re.findall(r"\\([A-Za-z]+)", s) if c not in KNOWN_COMMANDS}


def _strip_spaces(s: str) -> str:
    s = re.sub(r"\\[,;!: ]|\\quad|\\qquad", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def split_top(s: str, sep: str) -> list[str]:
    """Split on `sep` outside braces/brackets/parentheses."""
    out, depth, cur, i = [], 0, [], 0
    while i < len(s):
        c = s[i]
        if c in "{([":
            depth += 1
        elif c in "})]":
            depth = max(0, depth - 1)
        if depth == 0 and s.startswith(sep, i) and not s.startswith("\\" + sep, i - 1 if i else 0):
            out.append("".join(cur))
            cur = []
            i += len(sep)
            continue
        cur.append(c)
        i += 1
    out.append("".join(cur))
    return out


EVAL_RE = re.compile(
    r"^(?P<open>\\left\s*\[|\\left\s*\.|\\Big\[|\[)?\s*(?P<body>.*?)\s*"
    r"(?:\\right\s*[\]|]|\\Big\]|\\Big\||\]|\|)\s*"
    r"(?:_\s*\{?(?P<a1>[^{}^]*?)\}?\s*\^\s*\{?(?P<b1>[^{}]*?)\}?|\^\s*\{?(?P<b2>[^{}_]*?)\}?\s*_\s*\{?(?P<a2>[^{}]*?)\}?)\s*$",
    re.S,
)


def _parse(latex: str) -> sp.Expr:
    s = latex.replace("\\left(", "(").replace("\\right)", ")").replace("\\cdot", "*").replace("\\times", "*")
    e = parse_latex(s)
    # parse_latex reads \pi as a variable named "pi": make it the number, so sin(\pi) = 0 and 2\pi r check out
    # ("e" stays a variable: in handwriting it is as often a variable as Euler's number)
    return e.subs(sp.Symbol("pi"), sp.pi)  # type: ignore[no-any-return]


def parse_segment(latex: str) -> Segment:
    s = _strip_spaces(latex)
    if UNFINISHED.match(s):
        return Segment(latex, kind="unfinished")
    m = EVAL_RE.match(s)
    if m and m.group("body"):
        a = m.group("a1") or m.group("a2")
        b = m.group("b1") or m.group("b2")
        try:
            F = _parse(m.group("body"))
            lo, hi = _parse(a), _parse(b)
            var = next(iter(F.free_symbols), X) if F.free_symbols else X
            return Segment(
                latex, expr=sp.simplify(F.subs(var, hi) - F.subs(var, lo)), kind="evaluation", antiderivative=F, var=var
            )
        except Exception:
            pass
    if _unknown_commands(s):
        return Segment(latex, kind="unparsed")  # never grade what we can't read
    try:
        e = _parse(s)
    except Exception:
        return Segment(latex, kind="unparsed")
    if len(e.free_symbols) > 3:
        return Segment(latex, kind="unparsed")  # a word parsed as a product of letters
    if isinstance(e, sp.Integral):
        f = e.function
        var = e.limits[0][0] if e.limits else X
        return Segment(latex, expr=e.doit(), kind="integral", integrand=f, var=var)
    return Segment(latex, expr=e.doit() if hasattr(e, "doit") else e)


def equal(a: sp.Expr, b: sp.Expr) -> bool | None:
    """True/False if decidable; None if sympy can't tell."""
    try:
        d = sp.simplify(a - b)
        if d == 0:
            return True
        if d.is_number:
            return bool(abs(complex(d.evalf())) < 1e-9) or False
        # symbolic: probe a few points
        syms = sorted(d.free_symbols, key=str)
        vals = []
        for v in (0.37, 1.91, -2.3, 3.7):
            try:
                vals.append(abs(complex(d.subs({s: v for s in syms}).evalf())))
            except Exception:
                continue
        return all(x < 1e-9 for x in vals) if vals else None
    except Exception:
        return None


def pretty(e: sp.Expr | None) -> str:
    if e is None:
        return "?"
    try:
        return str(sp.nsimplify(e)) if e.is_number else str(e)
    except Exception:
        return str(e)


def check_chain(latex: str) -> ChainResult:
    """Check one '='-chain (a handwritten line plus any continuation lines starting with '=')."""
    text = " ".join(x.strip() for x in latex.splitlines() if x.strip())
    segs = [parse_segment(p) for p in split_top(text, "=")]
    res = ChainResult(segments=segs, complete=bool(segs) and segs[-1].kind not in ("unfinished",))
    prev: Segment | None = None
    for i, seg in enumerate(segs):
        if seg.kind in ("unfinished", "unparsed") or seg.expr is None:
            prev = None if seg.kind == "unparsed" else prev
            continue
        if prev is not None and prev.expr is not None:
            ok = equal(prev.expr, seg.expr)
            if ok is None:
                prev = seg
                continue
            detail = f"{pretty(prev.expr)} = {pretty(seg.expr)}" if ok else f"{pretty(prev.expr)} ≠ {pretty(seg.expr)}"
            if not ok and prev.kind == "integral" and seg.kind == "evaluation" and seg.antiderivative is not None:
                dF = sp.simplify(sp.diff(seg.antiderivative, seg.var))
                f = prev.integrand
                if f is not None and equal(dF, f) is False:
                    detail = (
                        f"wrong antiderivative: d/d{seg.var} ({seg.antiderivative}) = {dF}, not {f}; "
                        f"the integral is {pretty(prev.expr)}"
                    )
            res.steps.append(StepCheck(ok=bool(ok), index=i, detail=detail, expected=None if ok else pretty(prev.expr)))
        prev = seg
    return res


def check_equations(items: list[str]) -> list[StepCheck]:
    """Consecutive equations (e.g. '2x+5=4x ; 5=4x-2x ; 5=3x ; x=5/3') must keep the same solutions."""
    out: list[StepCheck] = []
    prev_sol: Any = None
    for i, it in enumerate(items):
        try:
            e = _parse(_strip_spaces(it))
        except Exception:
            prev_sol = None
            continue
        if not isinstance(e, sp.Equality):
            prev_sol = None
            continue
        syms = sorted(e.free_symbols, key=str)
        if len(syms) != 1:
            prev_sol = None
            continue
        sol = sp.solveset(e.lhs - e.rhs, syms[0], domain=sp.S.Complexes)
        if prev_sol is not None:
            ok = sol == prev_sol
            out.append(
                StepCheck(
                    ok=ok,
                    index=i,
                    kind="math.equation",
                    detail=f"solutions {sol} {'=' if ok else '≠'} {prev_sol}",
                    expected=None if ok else str(prev_sol),
                )
            )
        prev_sol = sol
    return out


def split_items(latex: str) -> list[str]:
    """A line with ';' (or '\\;'-separated equations) → items; a line with exactly one '=' per item."""
    text = latex.replace("\\;", ";")
    return [p.strip() for p in split_top(text, ";") if p.strip()]
