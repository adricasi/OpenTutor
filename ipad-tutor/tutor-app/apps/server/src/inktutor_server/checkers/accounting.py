"""ledger.balanced / ledger.taccount — deterministic bookkeeping checks (non-negotiable 4).

Journal entries: each handwritten line is one posting ("Dr Cash 1,200", "Cr Sales 1,200", or the two-column
layout with the credit indented / its amount in the right-hand column). Lines group into entries (debits
first, then credits; a new debit after a credit, or a line without an amount, starts a new entry). A
complete entry must have debits = credits. A "Total" line must equal the sums of the columns above it.

T-accounts: rows on the debit (left) and credit (right) side of the stem. Rows above the first rule are
postings. Below a rule, a row labelled "Bal"/"Balance"/"b/d" is the balance: it must equal |debits − credits|
and sit on the larger side. An unlabelled row below a rule is a total: it must equal the sum of the postings
on its side. A "c/d" posting is the balancing figure: it counts in the totals, not in the balance.

Amounts are exact (Decimal). "1,200", "1.200", "$1,200.50" and "12,50" are understood: a separator followed by
exactly three digits groups thousands, otherwise it is the decimal point (Spanish and English notation).
Whether an account is on the right side is a judgement about the transaction, not arithmetic: that stays
with the tutor (as a question).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Literal

Side = Literal["debit", "credit"]

_AMOUNT = re.compile(r"^[($€£]*-?\d[\d.,]*[)]?$")
DEBIT_WORDS = {"dr", "dr.", "debit", "debe"}
CREDIT_WORDS = {"cr", "cr.", "credit", "haber", "to", "a"}  # "To Sales", "a Ventas"
BALANCE_WORDS = {"bal", "bal.", "balance", "saldo", "b/d", "bd", "b/f", "bf"}
CARRIED_WORDS = {"c/d", "cd", "c/f", "cf"}
TOTAL_WORDS = {"total", "totals", "tot", "tot.", "sum", "suma", "total:"}


def parse_amount(tok: str) -> Decimal | None:
    """'1,200' → 1200, '1.200' → 1200, '12,50' → 12.50, '(500)' → -500; '(1)' (a reference) → None."""
    t = tok.strip().rstrip(".:;")
    if not t or not _AMOUNT.match(t):
        return None
    paren = t.startswith("(") and t.endswith(")")
    if paren and sum(ch.isdigit() for ch in t) <= 2:
        return None  # "(1)", "(12)": an entry / posting reference, not an amount
    neg = paren or "-" in t
    t = t.strip("()$€£-")
    if not t or not t[0].isdigit():
        return None
    seps = [i for i, ch in enumerate(t) if ch in ".,"]
    if seps:
        last = seps[-1]
        decimals = len(t) - last - 1
        if len({t[i] for i in seps}) == 2:  # both kinds: the last one is the decimal point
            t = re.sub(r"[.,]", "", t[:last]) + "." + t[last + 1 :]
        elif decimals == 3:  # 1,200 / 1.200 / 1,200,000: thousands
            t = re.sub(r"[.,]", "", t)
        elif len(seps) == 1:
            t = t.replace(",", ".")
        else:
            return None
    try:
        v = Decimal(t)
    except InvalidOperation:
        return None
    return -v if neg else v


def fmt(v: Decimal) -> str:
    """1200 → '1,200'; 12.5 → '12.50'."""
    q = v.quantize(Decimal("0.01"))
    return f"{q:,.0f}" if q == q.to_integral_value() else f"{q:,.2f}"


def _tokens(text: str) -> list[str]:
    return text.replace("|", " ").split()


@dataclass
class ParsedLine:
    kind: Literal["posting", "total", "arithmetic", "text"]
    side: Side | None  # from an explicit Dr/Cr word; None = decide by layout
    account: str
    amount: Decimal | None
    amounts: list[Decimal] = field(default_factory=list)
    label: str = ""  # "balance" | "carried" | "total" | ""


def parse_line(text: str) -> ParsedLine:
    """One handwritten line of a journal or one T-account row."""
    if "=" in text:
        return ParsedLine("arithmetic", None, "", None)
    toks = _tokens(text)
    side: Side | None = None
    label = ""
    words: list[str] = []
    amounts: list[Decimal] = []
    for i, tok in enumerate(toks):
        low = tok.lower().strip(":")
        a = parse_amount(tok)
        if a is not None:
            amounts.append(a)
        elif i == 0 and low in DEBIT_WORDS and len(toks) > 1:
            side = "debit"
        elif i == 0 and low in CREDIT_WORDS and len(toks) > 1:
            side = "credit"
        else:
            if low in BALANCE_WORDS:
                label = label or "balance"
            elif low in CARRIED_WORDS:
                label = "carried"
            elif low in TOTAL_WORDS:
                label = "total"
            words.append(tok)
    if label == "total":
        return ParsedLine("total", side, " ".join(words), amounts[-1] if amounts else None, amounts, label)
    if not amounts:
        return ParsedLine("text", side, " ".join(words), None, label=label)
    return ParsedLine("posting", side, " ".join(words), amounts[-1], amounts, label)


# ---------------------------------------------------------------- journal entries


@dataclass
class JournalLine:
    line_id: str
    text: str
    x: float  # left edge of the handwriting
    amount_x: float  # centre of the right-most ink word (the amount column)
    h: float  # character height on the page
    after_rule: bool = False  # a horizontal rule was drawn just above this line


@dataclass
class Posting:
    line_id: str
    side: Side
    account: str
    amount: Decimal
    how: str  # "word" (Dr/Cr written) | "indent" | "column" | "first"


@dataclass
class EntryCheck:
    line_ids: list[str]
    postings: list[Posting]
    debits: Decimal
    credits: Decimal
    complete: bool  # has both sides
    closed: bool  # another entry or a total follows: nothing more will be added
    ok: bool | None  # None: incomplete and still open

    @property
    def last(self) -> str:
        return self.line_ids[-1]


@dataclass
class TotalCheck:
    line_id: str
    ok: bool
    written: list[Decimal]
    expected: list[Decimal]  # [debits] or [debits, credits]
    detail: str


@dataclass
class JournalCheck:
    entries: list[EntryCheck]
    totals: list[TotalCheck]
    arithmetic: list[str]  # line ids with an "=" (checked by math.equivalent)
    sides: dict[str, Side] = field(default_factory=dict)


def check_journal(lines: list[JournalLine]) -> JournalCheck:
    """Group lines (top to bottom) into entries and check each one; check total lines against the columns."""
    entries: list[EntryCheck] = []
    totals: list[TotalCheck] = []
    arithmetic: list[str] = []
    sides: dict[str, Side] = {}
    cur: list[Posting] = []
    ref: JournalLine | None = None  # first unlabelled debit of the entry: the debit indent and amount column
    block: list[tuple[Posting, float]] = []  # postings since the last total line, with their amount column

    def close(closed: bool) -> None:
        nonlocal cur, ref
        if cur:
            d = sum((p.amount for p in cur if p.side == "debit"), Decimal(0))
            c = sum((p.amount for p in cur if p.side == "credit"), Decimal(0))
            complete = d > 0 and c > 0
            ok: bool | None = (d == c) if complete else (False if closed else None)
            entries.append(EntryCheck([p.line_id for p in cur], cur, d, c, complete, closed, ok))
        cur, ref = [], None

    for ln in lines:
        p = parse_line(ln.text)
        if p.kind == "arithmetic":
            arithmetic.append(ln.line_id)
            continue
        if p.kind == "total" or (p.kind == "posting" and ln.after_rule and not p.account):
            close(True)
            totals.append(_total(ln, p, block))
            block = []
            continue
        if p.kind == "text" or p.amount is None or _is_date(p):
            close(True)  # a date, an entry number or a narration line separates entries
            continue
        side: Side
        if p.side is not None:
            side, how = p.side, "word"
        elif ref is None:
            side, how = "debit", "first"
        elif ln.x - ref.x > 1.0 * ln.h:
            side, how = "credit", "indent"
        elif ln.amount_x - ref.amount_x > 1.5 * ln.h:
            side, how = "credit", "column"
        else:
            side, how = "debit", "column"
        if side == "debit" and any(q.side == "credit" for q in cur):
            close(True)  # a debit after the credits: the next entry
        if ref is None and side == "debit" and p.side is None:
            ref = ln
        posting = Posting(ln.line_id, side, p.account, p.amount, how)
        cur.append(posting)
        block.append((posting, ln.amount_x))
        sides[ln.line_id] = side
    close(False)
    return JournalCheck(entries, totals, arithmetic, sides)


MONTHS = {
    "jan",
    "feb",
    "mar",
    "apr",
    "may",
    "jun",
    "jul",
    "aug",
    "sep",
    "oct",
    "nov",
    "dec",
    "ene",
    "abr",
    "ago",
    "dic",
}


def _is_date(p: ParsedLine) -> bool:
    """'Jan 5', '3 May': a date heading, not a posting."""
    words = p.account.lower().split()
    return len(words) == 1 and words[0][:3] in MONTHS and p.amount is not None and 0 < p.amount < 32


def _total(ln: JournalLine, p: ParsedLine, block: list[tuple[Posting, float]]) -> TotalCheck:
    d = sum((q.amount for q, _ in block if q.side == "debit"), Decimal(0))
    c = sum((q.amount for q, _ in block if q.side == "credit"), Decimal(0))
    written = p.amounts[-2:]
    if len(written) == 2:
        expected = [d, c]
        detail = f"debits add up to {fmt(d)}, credits to {fmt(c)}"
    else:
        # one total: the column it is written in (nearest amount column), or either when they agree
        def dist(side: Side) -> float:
            xs = [x for q, x in block if q.side == side]
            return abs(ln.amount_x - sum(xs) / len(xs)) if xs else float("inf")

        side: Side = "debit" if dist("debit") <= dist("credit") else "credit"
        expected = [d if side == "debit" else c]
        detail = f"the {side} column adds up to {fmt(expected[0])}"
    ok = bool(written) and written == expected
    return TotalCheck(ln.line_id, ok, written, expected, detail)


# ---------------------------------------------------------------- T-accounts


@dataclass
class TRow:
    line_id: str
    side: Side
    text: str
    after: int  # how many rules (on this side) are above the row


@dataclass
class RowFinding:
    line_id: str
    ok: bool
    kind: str  # "total" | "balance" | "balance_side"
    label: str  # ≤ 8 words for the mark
    detail: str
    hints: dict[int, str] = field(default_factory=dict)


@dataclass
class TAccountCheck:
    name: str
    debits: Decimal
    credits: Decimal
    findings: list[RowFinding]
    postings: dict[str, tuple[Side, Decimal]]  # line id → posting

    @property
    def balance(self) -> tuple[Side | None, Decimal]:
        net = self.debits - self.credits
        return ("debit" if net > 0 else "credit" if net < 0 else None), abs(net)


def check_t_account(name: str, rows: list[TRow]) -> TAccountCheck:
    parsed = [(r, parse_line(r.text)) for r in rows]
    postings = {r.line_id: (r.side, p.amount) for r, p in parsed if r.after == 0 and p.amount is not None}
    side_sum: dict[Side, Decimal] = {"debit": Decimal(0), "credit": Decimal(0)}  # incl. c/d (for totals)
    net_sum: dict[Side, Decimal] = {"debit": Decimal(0), "credit": Decimal(0)}  # excl. c/d (for the balance)
    for r, p in parsed:
        if r.after == 0 and p.amount is not None:
            side_sum[r.side] += p.amount
            if p.label != "carried":
                net_sum[r.side] += p.amount
    d, c = net_sum["debit"], net_sum["credit"]
    out = TAccountCheck(name or "this account", d, c, [], postings)
    want_side, want = out.balance
    acct = name or "this account"
    for r, p in parsed:
        if r.after == 0 or p.amount is None:
            continue
        if p.label == "balance" or (p.label == "" and r.after >= 2):
            tail = f"{acct}: debits {fmt(d)}, credits {fmt(c)}"
            hints = {
                1: "Add up each side again: which side is bigger?",
                2: f"Debits add up to {fmt(d)} and credits to {fmt(c)}: the balance is the difference.",
            }
            if want_side is None:
                ok = p.amount == 0
                label, detail = ("", f"{tail}: balance nil") if ok else ("the sides are equal", f"{tail}: no balance")
                hints[3] = "Both sides are equal: the account has no balance."
            elif p.amount != want:
                ok, label = False, "check the balance"
                detail = f"{tail}; the balance is {fmt(want)} on the {want_side} side, not {fmt(p.amount)}"
                hints[3] = f"Balance = {fmt(max(d, c))} − {fmt(min(d, c))} = {fmt(want)} ({want_side} balance)."
            elif r.side != want_side:
                ok, label = False, f"a {want_side} balance"
                detail = f"{tail}; the balance of {fmt(want)} belongs on the {want_side} side"
                hints[3] = f"{fmt(want)} is a {want_side} balance: write it on the {want_side} side."
            else:
                ok, label, detail = True, "", f"{tail}; balance {fmt(want)} ({want_side})"
            hints[4] = f"Balance {fmt(want)} on the {want_side} side." if want_side else "No balance."
            out.findings.append(
                RowFinding(r.line_id, ok, "balance" if ok or p.amount != want else "balance_side", label, detail, hints)
            )
        else:
            total = side_sum[r.side]
            ok = p.amount == total
            amounts = [a for (s, a) in postings.values() if s == r.side]
            sum_txt = " + ".join(fmt(a) for a in amounts) or "0"
            out.findings.append(
                RowFinding(
                    r.line_id,
                    ok,
                    "total",
                    "" if ok else "check the total",
                    f"{acct}, {r.side} side: {sum_txt} = {fmt(total)}" + ("" if ok else f", written {fmt(p.amount)}"),
                    {
                        1: "Add the amounts above the line again.",
                        2: f"The {r.side} side has {len(amounts)} amounts: {sum_txt}.",
                        3: f"{sum_txt} = {fmt(total)}",
                        4: f"Total {fmt(total)}.",
                    },
                )
            )
    return out
