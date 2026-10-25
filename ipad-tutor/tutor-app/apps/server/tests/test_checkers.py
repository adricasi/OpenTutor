"""Checkers on real content: the drafted truth and the exact strings the tier-1 reader produced."""

import json

import pytest
from inktutor_server.checkers.arithmetic import check_long_division
from inktutor_server.checkers.math import check_chain, check_equations, split_items
from inktutor_server.config import Settings

ANN = Settings().annotations_dir


def truth(stem: str, i: int) -> list[str]:
    return json.loads((ANN / f"{stem}.json").read_text())["checkpoints"][i]["truth"].splitlines()


# what Qwen3.5-4B read from the real strokes (tier 1), verbatim
READ_INT1 = (
    r"\int _ { 2 } ^ { 4 } x ^ { 2 } + 3 \, d x = \left[ \frac { x ^ { 3 } } { 3 } + 3 x \right] _ { 2 } ^ { 4 } "
    r"= \frac { 4 ^ { 3 } } { 3 } + 3 \cdot 4 - \left( \frac { 2 ^ { 3 } } { 3 } + 3 \cdot 2 \right) = \square"
)
READ_INT2 = (
    r"\int _ { 0 } ^ { 1 } x ^ { 3 } d x = \left. \frac { x ^ { 5 } } { 3 } \right| _ { 0 } ^ { 1 } "
    r"= \frac { 1 ^ { 5 } } { 3 } = \frac { 1 } { 3 }"
)


@pytest.mark.parametrize("latex", ["\n".join(truth("rec-math-math-22r_69", 0)), READ_INT1])
def test_correct_integral_has_no_error_and_is_unfinished(latex):
    r = check_chain(latex)
    assert r.first_error is None
    assert [s.ok for s in r.steps] == [True, True]
    assert r.complete is False  # ends with an empty box


@pytest.mark.parametrize("latex", [truth("rec-math-math-22r_69", 1)[-1], READ_INT2])
def test_wrong_antiderivative_is_the_only_error(latex):
    r = check_chain(latex)
    err = r.first_error
    assert err is not None and err.index == 1  # between ∫ and [x⁵/3]₀¹
    assert "antiderivative" in err.detail and "5*x**4/3" in err.detail
    assert err.expected == "1/4"
    assert [s.ok for s in r.steps] == [False, True, True]  # later steps follow from the student's own step


def test_photo_equations_flag_the_step_where_the_solution_changes():
    line1 = truth_photo()[0]
    steps = check_equations(split_items(line1))
    assert [s.ok for s in steps] == [True, False, True]
    assert steps[1].index == 2  # "5 = 3x"


def test_photo_integral_wrong_antiderivative_unfinished():
    r = check_chain(truth_photo()[1])
    assert r.first_error is not None and r.first_error.index == 1
    assert "antiderivative" in r.first_error.detail
    assert r.complete is False or r.segments[-1].kind == "unfinished"


def truth_photo():
    d = json.loads((ANN / "rec-math-page-d9x_co.json").read_text())
    return next(iter(d["photos"].values()))["truth"].splitlines()


def test_long_division_correct_and_in_progress():
    assert check_long_division({"dividend": "250", "divisor": "25", "quotient": "10"}).ok
    r = check_long_division({"dividend": "250", "divisor": "25", "quotient": "1"})
    assert r.ok and not r.complete


def test_stuck_division_digit_too_small():
    r = check_long_division({"dividend": "248", "divisor": "4", "quotient": "5", "rows": ["20", "48"]})
    assert not r.ok and r.wrong_digit == 0 and r.expected_digit == "6"
    assert r.partial == 24 and r.remainder_if_written == 4
    assert "not smaller than 4" in r.detail
    assert r.expected_quotient == "62"


def test_unparseable_segments_are_skipped_not_marked():
    r = check_chain(r"\int_0^1 x\,dx = \frac{1}{2} = \weird{stuff}")
    assert r.first_error is None and [s.ok for s in r.steps] == [True]


@pytest.mark.parametrize(
    "latex,ok",
    [(r"\sin(\pi)=0", True), (r"\cos(\pi)=-1", True), (r"\sin(\pi)=1", False), (r"2\pi \cdot 3=6\pi", True)],
)
def test_pi_is_the_number_not_a_variable(latex, ok):
    from inktutor_server.checkers.math import check_chain

    r = check_chain(latex)
    assert all(st.ok for st in r.steps) is ok
