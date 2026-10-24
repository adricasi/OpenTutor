"""arithmetic.long_division — check a handwritten long division digit by digit.

Works on what the student wrote so far (a division in progress is normal): each quotient digit must be
the largest digit d with d·divisor ≤ the current partial dividend. The first wrong digit is the mistake;
a digit that is too small shows up as a remainder that is still ≥ the divisor (24 − 20 = 4 ≥ 4).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class DivisionCheck:
    ok: bool
    complete: bool
    detail: str
    wrong_digit: int | None = None  # index into the quotient string
    expected_digit: str | None = None
    partial: int | None = None  # partial dividend at the wrong digit
    remainder_if_written: int | None = None
    expected_quotient: str | None = None
    kind: str = "arithmetic.long_division"


def _int(s: object) -> int | None:
    t = "".join(ch for ch in str(s) if ch.isdigit())
    return int(t) if t else None


def correct_digits(dividend: int, divisor: int) -> list[tuple[int, int]]:
    """[(digit, partial dividend)] of the standard algorithm, skipping leading zeros."""
    out: list[tuple[int, int]] = []
    rem = 0
    started = False
    for ch in str(dividend):
        rem = rem * 10 + int(ch)
        if not started and rem < divisor:
            continue
        started = True
        out.append((rem // divisor, rem))
        rem %= divisor
    return out or [(0, dividend)]


def check_long_division(structure: dict[str, object]) -> DivisionCheck | None:
    dividend, divisor = _int(structure.get("dividend")), _int(structure.get("divisor"))
    quotient = "".join(ch for ch in str(structure.get("quotient", "")) if ch.isdigit())
    if dividend is None or not divisor:
        return None
    digits = correct_digits(dividend, divisor)
    expected = str(dividend // divisor)
    for i, ch in enumerate(quotient):
        if i >= len(digits):
            return DivisionCheck(
                ok=False,
                complete=True,
                detail=f"too many quotient digits: {dividend} ÷ {divisor} has {len(digits)}",
                wrong_digit=i,
                expected_quotient=expected,
            )
        want, partial = digits[i]
        if int(ch) != want:
            got = int(ch)
            rem = partial - got * divisor
            if rem >= divisor:
                why = (
                    f"{partial} − {got}·{divisor} = {rem}, which is not smaller than {divisor}: the digit is too small"
                )
            elif rem < 0:
                why = f"{got}·{divisor} = {got * divisor} is more than {partial}: the digit is too big"
            else:
                why = f"{partial} ÷ {divisor}: the digit should be {want}"
            return DivisionCheck(
                ok=False,
                complete=len(quotient) >= len(digits),
                detail=why,
                wrong_digit=i,
                expected_digit=str(want),
                partial=partial,
                remainder_if_written=rem,
                expected_quotient=expected,
            )
    complete = len(quotient) >= len(digits)
    detail = f"{dividend} ÷ {divisor} = {expected}" if complete else f"correct so far ({quotient} of {expected})"
    return DivisionCheck(ok=True, complete=complete, detail=detail, expected_quotient=expected)
