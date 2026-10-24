# Adapted from ipad-tutor/tutor-app/apps/server/src/inktutor_server/checkers/math.py
# (check_chain, equal, check_equations): same ideas - consecutive steps must stay equivalent,
# the first non-equivalent step is the mistake, equations compare solution sets, expressions
# compare by simplify() with a numeric-probe fallback. Differences: plain-text input instead of
# LaTeX, so every input first passes a strict allow-list tokenizer (ADR-0007), and the check runs
# in a resource-limited worker process with a 2-second timeout.
"""Deterministic maths checker (T-010, REQ-006 AC-006.6 ... AC-006.10, ADR-0007).

`check_attempt` never raises: anything it cannot read or finish in time is "can't tell"
(`decided=False`) and the caller falls back to the model's verdict (AC-006.9).
"""

from __future__ import annotations

import asyncio
import itertools
import math
import multiprocessing
import re
import resource
import string
import threading
from dataclasses import dataclass
from multiprocessing.connection import Connection
from multiprocessing.process import BaseProcess
from typing import Any, Literal

import sympy as sp
from sympy.parsing.sympy_parser import (
    convert_xor,
    parse_expr,
    rationalize,
    standard_transformations,
)

Verdict = Literal["correct", "partly_correct", "not_yet_correct"]

TIMEOUT_S = 2.0
STARTUP_TIMEOUT_S = 30.0
MEMORY_LIMIT_BYTES = 2 * 1024**3
CPU_LIMIT_S = 4

MAX_LENGTH = 300
MAX_NUMBER_DIGITS = 15
MAX_EXPONENT = 100
MAX_EXPONENT_PRODUCT = 10_000
FUNCTION_NAMES = ("sqrt", "sin", "cos", "tan", "log", "abs")
CONSTANT_NAMES = ("pi",)
# Uppercase letters that are sympy objects (E, I, N, O, Q, S) are not variables.
VARIABLE_LETTERS = frozenset(string.ascii_lowercase) | (
    frozenset(string.ascii_uppercase) - frozenset("EINOQS")
)
DIGITS = "0123456789"
NORMALISE = str.maketrans(
    {
        "\u00d7": "*",  # multiplication sign
        "\u00b7": "*",  # middle dot
        "\u00f7": "/",  # division sign
        "\u2212": "-",  # minus sign
        "\u2013": "-",  # en dash
        "\u00b2": "^2",
        "\u00b3": "^3",
        "\t": " ",
    }
)
PROBE_POINTS = (0.37, 1.91, -2.3, 3.7)
EPSILON = 1e-9


@dataclass(frozen=True)
class CheckResult:
    decided: bool
    verdict: Verdict | None
    first_error_step: str | None
    reason: str


def _undecided(reason: str) -> CheckResult:
    return CheckResult(False, None, None, reason)


class UnreadableError(Exception):
    """The input is outside what the checker accepts: it can't tell."""


# --------------------------------------------------------------------------------------
# Allow-list tokenizer: runs before anything reaches parse_expr (which uses eval).
# --------------------------------------------------------------------------------------

Token = tuple[str, str]  # (kind, text): num | var | func | const | op | lpar | rpar


def _tokenize(text: str) -> list[Token]:
    tokens: list[Token] = []
    i = 0
    while i < len(text):
        char = text[i]
        if char == " ":
            i += 1
        elif char in DIGITS:
            j = i
            while j < len(text) and text[j] in DIGITS:
                j += 1
            if j + 1 < len(text) and text[j] == "." and text[j + 1] in DIGITS:
                j += 1
                while j < len(text) and text[j] in DIGITS:
                    j += 1
            number = text[i:j]
            if len(number.replace(".", "")) > MAX_NUMBER_DIGITS:
                raise UnreadableError("number too long")
            tokens.append(("num", number))
            i = j
        elif char in "+-*/^":
            tokens.append(("op", char))
            i += 1
        elif char == "(":
            tokens.append(("lpar", char))
            i += 1
        elif char == ")":
            tokens.append(("rpar", char))
            i += 1
        elif char in string.ascii_letters:
            _reject_words(text, i)
            name = next(
                (n for n in (*FUNCTION_NAMES, *CONSTANT_NAMES) if text.startswith(n, i)), ""
            )
            if name:
                tokens.append(("func" if name in FUNCTION_NAMES else "const", name))
                i += len(name)
            elif char in VARIABLE_LETTERS:
                tokens.append(("var", char))
                i += 1
            else:
                raise UnreadableError(f"letter {char!r} is not allowed")
        else:
            raise UnreadableError(f"character {char!r} is not allowed")
    return tokens


def _reject_words(text: str, start: int) -> None:
    """Words such as "so" or "area" are prose, not products of variables: can't tell."""
    end = start
    while end < len(text) and text[end] in string.ascii_letters:
        end += 1
    run = text[start:end]
    if run in FUNCTION_NAMES or run in CONSTANT_NAMES:
        return
    standalone = (start == 0 or text[start - 1] == " ") and (end == len(text) or text[end] == " ")
    if len(run) >= 4 or (len(run) >= 2 and standalone):
        raise UnreadableError(f"{run!r} looks like a word")


def _operand_end(tokens: list[Token], start: int) -> int:
    """Index after the operand starting at `start` (a unary sign, then an atom)."""
    i = start
    while i < len(tokens) and tokens[i] in (("op", "-"), ("op", "+")):
        i += 1
    if i >= len(tokens):
        raise UnreadableError("missing exponent")
    kind = tokens[i][0]
    if kind in ("num", "var", "const"):
        return i + 1
    if kind == "func":
        i += 1
    if i < len(tokens) and tokens[i][0] == "lpar":
        depth = 0
        for j in range(i, len(tokens)):
            depth += {"lpar": 1, "rpar": -1}.get(tokens[j][0], 0)
            if depth == 0:
                return j + 1
    raise UnreadableError("bad exponent")


def _check_structure(tokens: list[Token]) -> None:
    depth = 0
    for index, (kind, _) in enumerate(tokens):
        if kind == "lpar":
            depth += 1
        elif kind == "rpar":
            depth -= 1
            if depth < 0:
                raise UnreadableError("unbalanced brackets")
        elif kind == "func" and (index + 1 >= len(tokens) or tokens[index + 1][0] != "lpar"):
            raise UnreadableError("a function needs brackets, like sqrt(x)")
        elif kind == "num" and index + 1 < len(tokens) and tokens[index + 1][0] == "num":
            raise UnreadableError("two numbers in a row")
    if depth != 0:
        raise UnreadableError("unbalanced brackets")
    powers = [i for i, token in enumerate(tokens) if token == ("op", "^")]
    if len(powers) > 6:
        raise UnreadableError("too many powers")
    product = 1.0
    for index in powers:
        end = _operand_end(tokens, index + 1)
        operand = tokens[index + 1 : end]
        if ("op", "^") in operand or (end < len(tokens) and tokens[end] == ("op", "^")):
            raise UnreadableError("tower of powers")
        inner = operand[1:-1] if operand and operand[0][0] == "lpar" else operand
        if len(inner) != 1 or inner[0][0] not in {"num", "var", "const"}:
            raise UnreadableError("exponent must be a number or a letter")
        numbers = [float(text) for kind, text in operand if kind == "num"]
        if any(n > MAX_EXPONENT for n in numbers):
            raise UnreadableError("exponent too large")
        if len(operand) == 1 and numbers:
            product *= max(numbers[0], 1.0)
        if product > MAX_EXPONENT_PRODUCT:
            raise UnreadableError("exponents too large")


def _to_source(tokens: list[Token]) -> str:
    """Python source with every implied multiplication written out as `*`."""
    parts: list[str] = []
    for index, (kind, text) in enumerate(tokens):
        if index:
            before = tokens[index - 1][0]
            if before in ("num", "var", "const", "rpar") and kind in (
                "num",
                "var",
                "const",
                "func",
                "lpar",
            ):
                parts.append("*")
        parts.append(text)
    return " ".join(parts)


def _log10(value: Any) -> sp.Expr:
    return sp.log(value, 10)  # school "log" is base 10


_LOCALS: dict[str, Any] = {
    **{letter: sp.Symbol(letter) for letter in VARIABLE_LETTERS},
    "sqrt": sp.sqrt,
    "sin": sp.sin,
    "cos": sp.cos,
    "tan": sp.tan,
    "log": _log10,
    "abs": sp.Abs,
    "pi": sp.pi,
}
# Empty `__builtins__`: with `evaluate=False` the generated code needs only these constructors.
_GLOBALS: dict[str, Any] = {
    "__builtins__": {},
    "Integer": sp.Integer,
    "Float": sp.Float,
    "Rational": sp.Rational,
    "Symbol": sp.Symbol,
    "Add": sp.Add,
    "Mul": sp.Mul,
    "Pow": sp.Pow,
}
_TRANSFORMS = (*standard_transformations, rationalize, convert_xor)


def _parse_expression(text: str) -> sp.Expr:
    if len(text) > MAX_LENGTH:
        raise UnreadableError("too long")
    tokens = _tokenize(text)
    if not tokens:
        raise UnreadableError("empty")
    _check_structure(tokens)
    try:
        parsed = parse_expr(
            _to_source(tokens),
            local_dict=dict(_LOCALS),
            global_dict=dict(_GLOBALS),
            transformations=_TRANSFORMS,
            evaluate=False,
        )
    except Exception as error:
        raise UnreadableError("cannot parse") from error
    if not isinstance(parsed, sp.Expr):
        raise UnreadableError("not an expression")
    if parsed.doit().has(sp.zoo, sp.nan, sp.oo, -sp.oo):
        raise UnreadableError("undefined value")
    return parsed


# --------------------------------------------------------------------------------------
# Equivalence
# --------------------------------------------------------------------------------------


def _equal(a: sp.Expr, b: sp.Expr) -> bool | None:
    """True/False when decidable, None when sympy can't tell (ipad-tutor `equal`)."""
    difference = sp.simplify(a - b)
    if difference == 0:
        return True
    if difference.is_number:
        value = complex(difference.evalf())
        if not math.isfinite(abs(value)):
            return None
        return abs(value) < EPSILON
    symbols = sorted(difference.free_symbols, key=str)
    sizes: list[float] = []
    for probe in PROBE_POINTS:
        try:
            size = abs(complex(difference.subs(dict.fromkeys(symbols, probe)).evalf()))
        except (TypeError, ValueError, ZeroDivisionError):
            continue
        if math.isfinite(size):
            sizes.append(size)
    if not sizes:
        return None
    return all(size < EPSILON for size in sizes)


@dataclass
class Step:
    text: str
    kind: Literal["expr", "eq"]
    expr: sp.Expr | None = None
    lhs: sp.Expr | None = None
    rhs: sp.Expr | None = None
    merged: sp.Set | None = None  # "x = 2, x = -2" written as one step
    merged_symbol: sp.Symbol | None = None

    @property
    def symbols(self) -> set[sp.Symbol]:
        if self.merged_symbol is not None:
            return {self.merged_symbol}
        parts = [self.expr] if self.kind == "expr" else [self.lhs, self.rhs]
        return {s for part in parts if part is not None for s in part.free_symbols}

    @property
    def solved_symbol(self) -> sp.Symbol | None:
        """The variable when the step reads `x = number` (or `number = x`), else None."""
        if self.merged_symbol is not None:
            return self.merged_symbol
        if self.kind == "eq" and self.lhs is not None and self.rhs is not None:
            return _solved_form(self.lhs, self.rhs)
        return None


def _solved_form(lhs: sp.Expr, rhs: sp.Expr) -> sp.Symbol | None:
    for variable, value in ((lhs, rhs), (rhs, lhs)):
        if isinstance(variable, sp.Symbol) and not value.free_symbols:
            return variable
    return None


def _solutions(step: Step) -> tuple[sp.Symbol, sp.Set] | None:
    """Solution set over the reals for an equation in one variable, else None."""
    if step.merged is not None and step.merged_symbol is not None:
        return step.merged_symbol, step.merged
    if step.lhs is None or step.rhs is None:
        return None
    symbols = step.symbols
    if len(symbols) != 1:
        return None
    (symbol,) = symbols
    solved = sp.solveset(step.lhs - step.rhs, symbol, domain=sp.S.Reals)
    known = isinstance(solved, sp.FiniteSet | sp.Interval | sp.Union) or solved in (
        sp.S.EmptySet,
        sp.S.Reals,
    )
    return (symbol, solved) if known else None


def _same_sets(a: sp.Set, b: sp.Set) -> bool | None:
    if a == b:
        return True
    if isinstance(a, sp.FiniteSet) and isinstance(b, sp.FiniteSet):
        if len(a) != len(b):
            return False
        for element in a:
            matches = [_equal(element, other) for other in b]
            if True not in matches:
                return None if None in matches else False
        return True
    try:
        return bool((a - b).is_empty and (b - a).is_empty)
    except (TypeError, ValueError):
        return None


def _equivalent(previous: Step, step: Step) -> bool | None:
    if previous.kind != step.kind:
        return None
    if step.kind == "expr" and previous.expr is not None and step.expr is not None:
        return _equal(previous.expr, step.expr)
    before, after = _solutions(previous), _solutions(step)
    if before is None or after is None or before[0] != after[0]:
        return None
    return _same_sets(before[1], after[1])


# --------------------------------------------------------------------------------------
# Splitting an attempt into steps
# --------------------------------------------------------------------------------------

EXERCISE_PREFIX = re.compile(
    r"^\s*(?:\(?\d+[.)]\s+)?(?:(?:please\s+)?(?:solve|simplify|calculate|compute|evaluate|"
    r"work out|find|expand|factori[sz]e)\b)?(?:\s+for\s+[a-z]\b)?\s*:?",
    re.IGNORECASE,
)


def _clean_exercise(text: str) -> str:
    cleaned = EXERCISE_PREFIX.sub("", text.translate(NORMALISE).replace("**", "^").strip(), count=1)
    return re.sub(r"[\s.?!:]+$", "", cleaned)


def _segments(item: str) -> list[str]:
    parts = [part.strip() for part in item.split("=")]
    if len(parts) > 1 and not parts[-1]:
        parts.pop()  # the student has not written the right-hand side yet
    if not all(parts):
        raise UnreadableError("empty side of an equals sign")
    return parts


def _steps_for_item(item: str, previous: Step | None) -> tuple[list[Step], str | None]:
    """Main-chain steps for one item; the second value is a failed side calculation."""
    texts = _segments(item)
    if len(texts) == 1 and item.rstrip().endswith("="):
        return [], None  # "x =": started but nothing to check yet
    exprs = [_parse_expression(text) for text in texts]
    has_symbols = any(e.free_symbols for e in exprs)
    equation_context = previous is None or previous.kind == "eq"
    if len(exprs) == 1:
        return [Step(texts[0], "expr", expr=exprs[0])], None
    if has_symbols and equation_context:
        if previous is None and len(exprs) > 2:
            raise UnreadableError("ambiguous chain")
        return [
            Step(f"{texts[0]} = {text}", "eq", lhs=exprs[0], rhs=expr)
            for text, expr in zip(texts[1:], exprs[1:], strict=True)
        ], None
    if not has_symbols and previous is not None and previous.kind == "eq":
        solved = _solutions(previous)
        if solved is not None and solved[1] == sp.S.EmptySet:
            raise UnreadableError(
                "a false statement can be the right answer to an impossible equation"
            )
        for before, after in itertools.pairwise(exprs):  # a side calculation
            if _equal(before, after) is False:
                return [], item
        return [], None
    return [Step(text, "expr", expr=expr) for text, expr in zip(texts, exprs, strict=True)], None


def _merged_solutions(items: list[str]) -> Step | None:
    """`x = 2, x = -2` on one line is one step stating a solution set."""
    if len(items) < 2:
        return None
    values: list[sp.Expr] = []
    variable: sp.Symbol | None = None
    for item in items:
        parts = _segments(item)
        if len(parts) != 2:
            return None
        lhs, rhs = (_parse_expression(part) for part in parts)
        symbol = _solved_form(lhs, rhs)
        if symbol is None or (variable is not None and symbol != variable):
            return None
        variable = symbol
        values.append(sp.simplify(rhs if lhs == symbol else lhs))
    return Step(", ".join(items), "eq", merged=sp.FiniteSet(*values), merged_symbol=variable)


def _line_items(attempt: str) -> list[list[str]]:
    lines: list[str] = []
    for raw in attempt.translate(NORMALISE).replace("**", "^").splitlines():
        line = raw.strip()
        if not line:
            continue
        if re.search(r"[0-9],[0-9]", line):
            raise UnreadableError("a decimal comma is ambiguous")
        if line.startswith("=") and lines:
            lines[-1] += " " + line  # continuation of the previous line's chain
        else:
            lines.append(line)
    return [[item.strip() for item in re.split(r"[;,]", line) if item.strip()] for line in lines]


def _parse_exercise(exercise_text: str) -> Step | None:
    cleaned = _clean_exercise(exercise_text)
    if not cleaned or re.search(r"[;,\n]", cleaned):
        return None
    try:
        texts = _segments(cleaned)
        exprs = [_parse_expression(text) for text in texts]
    except UnreadableError:
        return None
    if len(exprs) == 1:
        return Step(texts[0], "expr", expr=exprs[0])
    if len(exprs) == 2 and any(e.free_symbols for e in exprs):
        return Step(cleaned, "eq", lhs=exprs[0], rhs=exprs[1])
    return None


def check_sync(exercise_text: str, exercise_typed: bool, attempt: str) -> CheckResult:
    """The whole check, in this process. Use `check_attempt` from application code."""
    try:
        return _check(exercise_text, exercise_typed, attempt)
    except UnreadableError as error:
        return _undecided(f"can't read the attempt: {error}")
    except Exception as error:
        return _undecided(f"checker error: {type(error).__name__}")


def _read_attempt(attempt: str, exercise: Step | None) -> list[Step | str]:
    """Every item of the attempt, read before anything is judged.

    Main-chain steps come as `Step`; a wrong side calculation comes as its text.
    """
    entries: list[Step | str] = []
    previous = exercise
    for items in _line_items(attempt):
        merged = _merged_solutions(items)
        if merged:
            entries.append(merged)
            previous = merged
            continue
        for item in items:
            steps, bad_side_calculation = _steps_for_item(item, previous)
            if bad_side_calculation:
                entries.append(bad_side_calculation)
            entries.extend(steps)
            previous = steps[-1] if steps else previous
    return entries


def _check(exercise_text: str, exercise_typed: bool, attempt: str) -> CheckResult:
    if len(attempt) > MAX_LENGTH or len(exercise_text) > MAX_LENGTH:
        return _undecided("input too long")
    exercise = _parse_exercise(exercise_text) if exercise_typed else None
    entries = _read_attempt(attempt, exercise)
    steps = [entry for entry in entries if isinstance(entry, Step)]
    if not entries:
        return _undecided("no steps to check")
    reference = exercise.symbols if exercise else (steps[0].symbols if steps else set())
    if any(not step.symbols <= reference for step in steps):
        raise UnreadableError("a step uses a variable that is not in the exercise")
    previous = exercise
    for entry in entries:
        if isinstance(entry, str):
            return CheckResult(True, "not_yet_correct", entry, "A calculation is wrong.")
        if previous is not None:
            same = _equivalent(previous, entry)
            if same is None:
                raise UnreadableError("cannot compare two steps")
            if not same:
                return CheckResult(
                    True,
                    "not_yet_correct",
                    entry.text,
                    f"'{entry.text}' is not equivalent to the step before it.",
                )
        previous = entry
    if previous is None or not steps:
        return _undecided("no steps to check")
    return _final_verdict(exercise, previous)


def _final_verdict(exercise: Step | None, last: Step) -> CheckResult:
    """All steps are equivalent: decide only for a typed, solvable equation (AC-006.7)."""
    solved = _solutions(exercise) if exercise is not None and exercise.kind == "eq" else None
    if solved is None or not isinstance(solved[1], sp.FiniteSet) or not solved[1]:
        return _undecided("steps are equivalent; the final answer is left to the model")
    if last.solved_symbol is not None:
        return CheckResult(True, "correct", None, "The last step states the solution.")
    return CheckResult(
        True, "partly_correct", None, "The steps are right but the solution is not reached yet."
    )


# --------------------------------------------------------------------------------------
# Worker process: memory/CPU limits in the child, 2-second timeout, kill and respawn.
# --------------------------------------------------------------------------------------


def _apply_limits(memory_bytes: int) -> None:
    """Called in the child process only."""
    resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
    _reset_cpu_limit()


def _reset_cpu_limit() -> None:
    usage = resource.getrusage(resource.RUSAGE_SELF)
    used = math.ceil(usage.ru_utime + usage.ru_stime)
    resource.setrlimit(resource.RLIMIT_CPU, (used + CPU_LIMIT_S, resource.RLIM_INFINITY))


def _worker_main(connection: Connection, memory_bytes: int) -> None:  # pragma: no cover
    _apply_limits(memory_bytes)
    connection.send("ready")
    while True:
        try:
            request = connection.recv()
        except EOFError:
            return
        _reset_cpu_limit()
        connection.send(check_sync(*request))


class _Worker:
    """One long-lived child process, started lazily; the lock serialises requests."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._process: BaseProcess | None = None
        self._connection: Connection | None = None

    def _start(self) -> Connection:
        context = multiprocessing.get_context("spawn")
        parent, child = context.Pipe()
        process = context.Process(
            target=_worker_main, args=(child, MEMORY_LIMIT_BYTES), daemon=True
        )
        process.start()
        child.close()
        self._process, self._connection = process, parent
        if not parent.poll(STARTUP_TIMEOUT_S) or parent.recv() != "ready":
            raise OSError("maths worker did not start")
        return parent

    def stop(self) -> None:
        process, connection = self._process, self._connection
        self._process = self._connection = None
        if process is not None:
            process.kill()
            process.join(5)
        if connection is not None:
            connection.close()

    def run(self, request: tuple[str, bool, str], timeout_s: float) -> CheckResult:
        with self._lock:
            try:
                alive = self._process is not None and self._process.is_alive()
                connection = self._connection if alive and self._connection else self._start()
                connection.send(request)
                if not connection.poll(timeout_s):
                    self.stop()
                    return _undecided("the checker took too long")
                result = connection.recv()
            except (EOFError, OSError, ValueError):
                self.stop()
                return _undecided("the checker stopped")
            if not isinstance(result, CheckResult):
                self.stop()
                return _undecided("the checker answered badly")
            return result


_worker = _Worker()


def shutdown() -> None:
    """Stop the worker process (it is started again on the next check)."""
    _worker.stop()


async def check_attempt(
    exercise_text: str, exercise_typed: bool, attempt: str, timeout_s: float = TIMEOUT_S
) -> CheckResult:
    """Check a typed maths attempt without blocking the event loop; never raises."""
    if len(attempt) > MAX_LENGTH or len(exercise_text) > MAX_LENGTH:
        return _undecided("input too long")
    try:
        return await asyncio.to_thread(
            _worker.run, (exercise_text, exercise_typed, attempt), timeout_s
        )
    except Exception as error:
        return _undecided(f"checker error: {type(error).__name__}")
