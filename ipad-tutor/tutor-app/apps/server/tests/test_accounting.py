"""Accounting mode (Phase 10): T-account / journal geometry, ledger checkers and the marks they own, on synthetic
fixtures (ink-tutor/tools/synthetic/generate.py) with a fake reader fed by their ground truth.

No real recording of accounting exists yet; real handwriting will need its own fixture.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from inktutor_server.agent import agent as agent_mod
from inktutor_server.agent.page_checks import arithmetic_latex
from inktutor_server.app import create_app
from inktutor_server.checkers.accounting import (
    JournalLine,
    TRow,
    check_journal,
    check_t_account,
    parse_amount,
    parse_line,
)
from inktutor_server.checkers.math import check_chain
from inktutor_server.config import Settings
from inktutor_server.fixtures import page_strokes_at
from inktutor_server.ink.ledger import ledger_layout
from inktutor_server.modes import load_mode
from synthetic import checkpoint_t, fake_reader, feedback, fixture, run, text_of, truth_lines, visible
from test_agent import FakeRuntime, FakeTutor

JOURNAL, TACCOUNTS = "synthetic-accounting-journal", "synthetic-accounting-taccounts"


# ---------------------------------------------------------------- checker


@pytest.mark.parametrize(
    "tok,want",
    [
        ("1,200", "1200"),
        ("1.200", "1200"),
        ("12,50", "12.50"),
        ("$1,200.50", "1200.50"),
        ("1.234,56", "1234.56"),
        ("(500)", "-500"),
        ("(1)", None),
        ("CASH", None),
    ],
)
def test_amounts_in_english_and_spanish_notation(tok, want):
    got = parse_amount(tok)
    assert (str(got) if got is not None else None) == want


def test_line_parsing():
    p = parse_line("Dr Cash 1,200")
    assert (p.kind, p.side, p.account, p.amount) == ("posting", "debit", "Cash", Decimal(1200))
    assert parse_line("To Sales 500").side == "credit"
    assert parse_line("a Ventas 500").side == "credit"
    assert parse_line("Bal c/d 300").label == "carried"
    assert parse_line("BAL 450").label == "balance"
    assert parse_line("Total 1,500 1,500").kind == "total"
    assert parse_line("200 + 300 = 500").kind == "arithmetic"
    assert parse_line("(1)").kind == "text"


def _jl(i, text, x=60, ax=300):
    return JournalLine(i, text, x, ax, 26)


def test_entries_group_and_balance():
    jc = check_journal(
        [
            _jl("a", "Dr Cash 1,200"),
            _jl("b", "Cr Sales 1,200", 100),
            _jl("c", "Dr Rent 300"),
            _jl("d", "Dr Utilities 150"),
            _jl("e", "Cr Cash 400", 100),
            _jl("f", "Equipment 800", 60, 420),
            _jl("g", "Cash 800", 60, 540),  # not indented: the amount column says credit
            _jl("h", "Supplies 90", 60, 420),
        ]
    )
    assert [(e.line_ids, e.ok) for e in jc.entries] == [
        (["a", "b"], True),
        (["c", "d", "e"], False),
        (["f", "g"], True),
        (["h"], None),  # last entry, only a debit so far: still being written, no verdict
    ]
    assert jc.sides["g"] == "credit" and jc.entries[2].postings[1].how == "column"


def test_entry_without_a_credit_is_wrong_once_another_starts():
    jc = check_journal([_jl("a", "Dr Rent 300"), _jl("b", "Jan 5"), _jl("c", "Dr Cash 100"), _jl("d", "Cr Sales 100")])
    assert [(e.line_ids, e.ok, e.complete) for e in jc.entries] == [(["a"], False, False), (["c", "d"], True, True)]


def test_total_line_checks_both_columns():
    lines = [_jl("a", "Dr Cash 500", ax=300), _jl("b", "Cr Sales 500", 100, 420), _jl("t", "Total 500 400")]
    t = check_journal(lines).totals[0]
    assert not t.ok and t.expected == [Decimal(500), Decimal(500)]
    lines[-1] = JournalLine("t", "500 500", 60, 360, 26, after_rule=True)  # unlabelled, under a rule
    assert check_journal(lines).totals[0].ok


def test_t_account_balance_total_and_side():
    rows = [TRow("1", "debit", "500", 0), TRow("2", "debit", "200", 0), TRow("3", "credit", "250", 0)]
    ok = check_t_account("Cash", [*rows, TRow("4", "debit", "Bal 450", 1)])
    assert ok.balance == ("debit", Decimal(450)) and ok.findings[0].ok
    wrong = check_t_account("Cash", [*rows, TRow("4", "debit", "Bal 550", 1)]).findings[0]
    assert not wrong.ok and wrong.label == "check the balance" and "450" in wrong.hints[3]
    side = check_t_account("Cash", [*rows, TRow("4", "credit", "Bal 450", 1)]).findings[0]
    assert not side.ok and side.kind == "balance_side" and side.label == "a debit balance"
    total = check_t_account(
        "Rev", [TRow("1", "credit", "500", 0), TRow("2", "credit", "300", 0), TRow("3", "credit", "700", 1)]
    )
    assert not total.findings[0].ok and total.findings[0].hints[3] == "500 + 300 = 800"


def test_british_style_carried_down_balance():
    rows = [
        TRow("1", "debit", "500", 0),
        TRow("2", "credit", "200", 0),
        TRow("3", "credit", "Bal c/d 300", 0),  # balancing figure: counts in the totals, not in the balance
        TRow("4", "debit", "500", 1),
        TRow("5", "credit", "500", 1),
        TRow("6", "debit", "Bal b/d 300", 2),
    ]
    tc = check_t_account("Cash", rows)
    assert [f.ok for f in tc.findings] == [True, True, True]


def test_arithmetic_lines_reuse_the_math_checker():
    assert arithmetic_latex("1,200 + 450 = 1,650") == "1200 + 450 = 1650"
    assert check_chain(arithmetic_latex("1200 + 450 + 800 = 2350")).first_error is not None
    assert check_chain(arithmetic_latex("3 x 150 = 450")).first_error is None


# ---------------------------------------------------------------- geometry on the synthetic fixtures


def test_t_accounts_are_found_with_their_sides_and_rules():
    strokes = page_strokes_at(fixture(TACCOUNTS), 1e12)
    lay = ledger_layout(strokes)
    truth = truth_lines(TACCOUNTS)
    assert len(lay.taccounts) == 3
    rows = {
        (text_of(truth, r.stroke_ids), r.parts["side"][0], r.parts["after"][0])
        for r in lay.regions
        if r.kind == "t_row"
    }
    assert rows == {
        ("500", "debit", "0"),
        ("200", "debit", "0"),
        ("250", "credit", "0"),
        ("BAL 450", "debit", "1"),
        ("500", "credit", "0"),
        ("300", "credit", "0"),
        ("700", "credit", "1"),
        ("300", "debit", "0"),
        ("BAL 300", "credit", "1"),
    }
    titles = {text_of(truth, r.stroke_ids) for r in lay.regions if r.kind == "t_title"}
    assert titles == {"CASH", "REVENUE", "SUPPLIES"}
    journal = [text_of(truth, r.stroke_ids) for r in lay.regions if r.kind == "line"]
    assert journal == ["DR CASH 500", "CR REVENUE 500"]
    # every region is exactly one ground-truth line (nothing split, nothing merged)
    for r in lay.regions:
        assert any(set(r.stroke_ids) == set(ln["strokes"]) for ln in truth)


def test_journal_rows_join_name_and_amount_column():
    lay = ledger_layout(page_strokes_at(fixture(JOURNAL), 1e12))
    truth = truth_lines(JOURNAL)
    assert lay.taccounts == []
    assert [text_of(truth, r.stroke_ids) for r in lay.regions] == [ln["text"] for ln in truth]
    for r in lay.regions:
        assert any(set(r.stroke_ids) == set(ln["strokes"]) for ln in truth)


# ---------------------------------------------------------------- the agent, end to end (fake reader + tutor)


def make_client(tmp_path, monkeypatch, stem):
    monkeypatch.setattr(agent_mod.Agent, "_read", fake_reader(stem, "text"))
    m = load_mode("accounting")
    fast = m.__class__(**{**m.__dict__, "settle_ms": 150})
    monkeypatch.setattr(agent_mod, "load_mode", lambda name: fast)

    def line(text):
        return lambda c: next((x.id for x in c.lines if x.text == text), "L_none")

    tutor = FakeTutor(
        [
            {"type": "mark", "line_id": line("DR CASH 500"), "glyph": "cross", "text": "wrong account?"},
            {"type": "mark", "line_id": line("CR REVENUE 500"), "glyph": "check", "text": "tutor tick"},
            {"type": "mark", "line_id": line("700"), "glyph": "question", "text": "on a decided line"},
        ]
    )
    app = create_app(Settings(data_dir=tmp_path, tier1_enabled=False, tier2_provider="none"))
    client = TestClient(app)
    client.__enter__()
    app.state.svc.runtime = FakeRuntime(tutor)
    return client


@pytest.fixture
def journal_client(tmp_path, monkeypatch):
    c = make_client(tmp_path, monkeypatch, JOURNAL)
    yield c
    c.__exit__(None, None, None)


@pytest.fixture
def taccount_client(tmp_path, monkeypatch):
    c = make_client(tmp_path, monkeypatch, TACCOUNTS)
    yield c
    c.__exit__(None, None, None)


def _marks_by_text(fb, stem):
    """{(glyph, line text)} of the marks still on the page."""
    truth = truth_lines(stem)
    out = set()
    for f in visible(fb):
        if f["type"] != "mark":
            continue
        line = f["anchor"]["line_id"]
        sid = line.removeprefix("L_")
        text = next(ln["text"] for ln in truth if sid in ln["strokes"])
        out.add((f["glyph"], text, f["source"]))
    return out


def test_journal_checkpoints(journal_client):
    fb = feedback(run(journal_client, JOURNAL, "accounting", checkpoint_t(JOURNAL, 1)))
    marks = _marks_by_text(fb, JOURNAL)
    assert ("check", "CR SALES 1,200", "checker") in marks
    assert ("cross", "CR CASH 400", "checker") in marks
    cross = next(f for f in visible(fb) if f["type"] == "mark" and f["glyph"] == "cross")
    assert cross["text"] == "debits ≠ credits"


def test_journal_final_two_column_entry_and_wrong_sum(journal_client):
    fb = feedback(run(journal_client, JOURNAL, "accounting", 1e12))
    marks = _marks_by_text(fb, JOURNAL)
    checker = {(g, t) for g, t, s in marks if s == "checker"}
    assert checker == {
        ("check", "CR SALES 1,200"),
        ("cross", "CR CASH 400"),
        ("check", "CASH 800"),
        ("cross", "1200 + 450 + 800 = 2350"),
        ("underline", "1200 + 450 + 800 = 2350"),
    }
    underline = next(f for f in visible(fb) if f["type"] == "mark" and f["glyph"] == "underline")
    sum_line = next(ln for ln in truth_lines(JOURNAL) if ln["text"].startswith("1200"))
    # the underline sits under "2350" only: the strokes after the "=" sign
    assert set(underline["anchor"]["stroke_ids"]) == set(sum_line["strokes"][-5:])


def test_t_accounts_final(taccount_client):
    fb = feedback(run(taccount_client, TACCOUNTS, "accounting", 1e12))
    marks = _marks_by_text(fb, TACCOUNTS)
    checker = {(g, t) for g, t, s in marks if s == "checker"}
    assert checker == {
        ("check", "CR REVENUE 500"),
        ("check", "BAL 450"),
        ("cross", "700"),
        ("cross", "BAL 300"),
    }
    tutor = {(g, t) for g, t, s in marks if s == "tutor"}
    assert tutor == {("question", "DR CASH 500")}  # ✗ → question; a tutor ✓ dropped; decided lines left alone


def test_t_account_hint_is_deterministic_at_level_3(taccount_client):
    def ask(ws, inbox, page_id, got):
        cross = next(f for f in visible(feedback(got)) if f["type"] == "mark" and f.get("text") == "check the total")
        ws.send_json(
            {
                "type": "hint.request",
                "page_id": page_id,
                "feedback_id": cross["id"],
                "line_id": cross["anchor"]["line_id"],
                "level": 3,
            }
        )
        return inbox.until(lambda m, _: m["type"] == "status" and m["phase"] in ("waiting", "error"), 5)

    fb = feedback(run(taccount_client, TACCOUNTS, "accounting", 1e12, then=ask))
    assert [f["text"] for f in fb if f["type"] == "hint"] == ["500 + 300 = 800"]
