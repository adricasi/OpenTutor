"""Deterministic maths checker (T-010, AC-006.6 ... AC-006.10, ADR-0007)."""

import asyncio
import json
import multiprocessing
from collections.abc import Iterator
from multiprocessing.connection import Connection
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from opentutor.checker import maths
from opentutor.checker.maths import CheckResult, check_attempt, check_sync
from opentutor.model import FakeModel
from tests.conftest import join, photo_bytes
from tests.test_app import analysis, events, send

EQUATION = "Solve 2x + 3 = 11"


@pytest.fixture(scope="module", autouse=True)
def _stop_worker() -> Iterator[None]:
    yield
    maths.shutdown()


def decided(result: CheckResult, verdict: str, first_error: str | None = None) -> None:
    assert result.decided, result.reason
    assert result.verdict == verdict
    assert result.first_error_step == first_error


def undecided(result: CheckResult) -> None:
    assert not result.decided
    assert result.verdict is None
    assert result.first_error_step is None


# AC-006.6 -----------------------------------------------------------------------------


def test_ac_006_6_first_non_equivalent_step() -> None:
    result = check_sync(EQUATION, True, "2x = 14\nx = 7")
    decided(result, "not_yet_correct", "2x = 14")


def test_ac_006_6_later_steps_are_judged_against_the_students_previous_step() -> None:
    # 2x = 14 is wrong; x = 7 follows from it, so only the first mistake is reported.
    first = check_sync(EQUATION, True, "2x = 14\nx = 7")
    assert first.first_error_step == "2x = 14"
    second = check_sync(EQUATION, True, "2x = 8\nx = 5")
    decided(second, "not_yet_correct", "x = 5")


def test_ac_006_6_expression_chain_in_one_line() -> None:
    decided(check_sync("", False, "2*3+4 = 6+4 = 11"), "not_yet_correct", "11")
    undecided(check_sync("", False, "2*3+4 = 6+4 = 10"))


def test_ac_006_6_expressions_equal_when_difference_simplifies_to_zero() -> None:
    result = check_sync("Simplify (x+1)^2 - x^2", True, "x^2 + 2x + 1 - x^2\n2x + 1\n2x + 2")
    decided(result, "not_yet_correct", "2x + 2")
    undecided(check_sync("Simplify (x+1)^2 - x^2", True, "x^2 + 2x + 1 - x^2\n2x + 1"))


def test_ac_006_6_probe_fallback_for_radicals() -> None:
    undecided(check_sync("", False, "sqrt(8)*x\n2*sqrt(2)*x"))
    decided(check_sync("", False, "sqrt(8)*x\n3*sqrt(2)*x"), "not_yet_correct", "3*sqrt(2)*x")


def test_ac_006_6_equations_equivalent_when_same_solution_set() -> None:
    result = check_sync("Solve x^2 = 4", True, "x^2 - 4 = 0\n(x-2)(x+2) = 0\nx = 2, x = -2")
    decided(result, "correct")


def test_ac_006_6_plain_text_notation_variants() -> None:
    decided(check_sync("Solve x/2 = 3", True, "x = 6"), "correct")
    decided(check_sync("Solve 0.5x = 3", True, "x = 6"), "correct")
    decided(check_sync("Solve x**2 = 9 ", True, "x = 3, x = -3"), "correct")
    decided(check_sync("Solve 3 \u00d7 x = 12", True, "x = 12 \u00f7 3"), "correct")
    decided(check_sync("Solve sqrt(x) = 3", True, "x = 9"), "correct")


def test_ac_006_6_side_calculation_is_checked() -> None:
    decided(check_sync(EQUATION, True, "2x = 11 - 3 = 7"), "not_yet_correct", "2x = 7")
    decided(check_sync(EQUATION, True, "2x = 11 - 3\n11 - 3 = 7"), "not_yet_correct", "11 - 3 = 7")


def test_ac_006_6_continuation_line_starting_with_equals() -> None:
    decided(check_sync(EQUATION, True, "2x = 11 - 3\n= 8\nx = 8/2\nx = 4"), "correct")


# AC-006.7 -----------------------------------------------------------------------------


def test_ac_006_7_solved_equation_is_correct() -> None:
    decided(check_sync(EQUATION, True, "2x + 3 - 3 = 11 - 3\n2x = 8\nx = 4"), "correct")
    decided(check_sync(EQUATION, True, "x = 4"), "correct")


def test_ac_006_7_correct_needs_every_solution() -> None:
    decided(check_sync("x^2 = 4", True, "x = 2"), "not_yet_correct", "x = 2")


def test_ac_006_7_unfinished_but_right_is_partly_correct() -> None:
    decided(check_sync(EQUATION, True, "2x = 8"), "partly_correct")
    decided(check_sync(EQUATION, True, "2x = 8\nx ="), "partly_correct")


def test_ac_006_7_wrong_final_answer() -> None:
    decided(check_sync(EQUATION, True, "2x = 8\nx = 5"), "not_yet_correct", "x = 5")


def test_ac_006_7_photo_exercise_checks_steps_but_not_the_final_answer() -> None:
    # The exercise text came from the model: it is ignored, even if wrong.
    wrong_reading = "Solve 5x = 1"
    undecided(check_sync(wrong_reading, False, "2x + 3 = 11\n2x = 8\nx = 4"))
    result = check_sync(wrong_reading, False, "2x + 3 = 11\n2x = 9\nx = 4")
    decided(result, "not_yet_correct", "2x = 9")


def test_ac_006_7_expression_exercise_final_answer_is_left_to_the_model() -> None:
    undecided(check_sync("Simplify 2x + 3x + 4", True, "5x + 4"))


def test_ac_006_7_no_solution_equation_is_not_called_wrong() -> None:
    undecided(check_sync("Solve 3x = 3x + 1", True, "0 = 1"))


# AC-006.9: cannot tell ----------------------------------------------------------------


@pytest.mark.parametrize(
    "attempt",
    [
        "so the answer is x = 4",
        "I think it is four",
        "x = 4 cm",
        "2x = 8; ???",
        "",
        "   \n  ",
        "x = ",
        "=",
        "sqrt 4",
        "x = 1/0",
        "2 3",
        "((x)",
        "x)",
        "x = 4.",
        "x = .5",
        "x = 4,5",
        "y = 2x + 1",
    ],
)
def test_ac_006_9_unreadable_attempts_are_undecided(attempt: str) -> None:
    undecided(check_sync(EQUATION, True, attempt))


def test_ac_006_9_unreadable_exercise_degrades_to_step_checking() -> None:
    result = check_sync("Find the area of the triangle", True, "3x + 1 = 7\n3x = 6\nx = 2")
    undecided(result)
    decided(
        check_sync("Find the area of the triangle", True, "3x + 1 = 7\n3x = 5"),
        "not_yet_correct",
        "3x = 5",
    )


# Hostile inputs -----------------------------------------------------------------------


def test_hostile_inputs_never_execute_code(tmp_path: Path) -> None:
    marker = tmp_path / "pwned"
    hostile = [
        f"__import__('os').system('touch {marker}')",
        "x.__class__",
        "().__class__",
        "lambda: 1",
        "(lambda x: x)(1)",
        "9^9^9^9",
        "2**2**2",
        "(9^9)^(9^9)",
        "x^1000",
        "99999999999999999999",
        "E",
        "I",
        "N(1)",
        "S(1)",
        "O(x)",
        "Q",
        "exec('1')",
        "open('f')",
        "x" * 10_000,
        "1+" * 200 + "1",
        "sqrt(" * 100 + "1" + ")" * 100,
        "x;import os",
        "x²²²²²²²",
        "x.y",
        "x[0]",
        "{1}",
        "'a'",
        "x if 1 else 2",
        "1e5",
        "0x10",
        "sin(x)(1)",
    ]
    for text in hostile:
        for attempt in (text, f"x = 1\n{text}", f"{text}\nx = 1"):
            result = check_sync(EQUATION, True, attempt)
            assert result.verdict != "correct", attempt
            assert not marker.exists()
    undecided(check_sync(EQUATION, True, "x = 1\n9^9^9^9"))
    undecided(check_sync("9^9^9^9", True, "x = 1"))
    undecided(check_sync(EQUATION, True, "10000" * 100))


def test_tokenizer_allow_list() -> None:
    tokens = maths._tokenize("2x+sqrt(pi)")
    assert [t[0] for t in tokens] == ["num", "var", "op", "func", "lpar", "const", "rpar"]
    for bad in ("E", "I", "N", "O", "Q", "S", "_", "a.b", "1.", ".5", "#", "x!", "x%", "'"):
        with pytest.raises(maths.UnreadableError):
            maths._tokenize(bad)
    with pytest.raises(maths.UnreadableError):
        maths._parse_expression("x" * (maths.MAX_LENGTH + 1))


def test_parser_namespace_has_no_builtins() -> None:
    assert maths._GLOBALS["__builtins__"] == {}
    assert not set(maths._LOCALS) & set("EINOQS")


# Worker process: AC-006.9 timeout -----------------------------------------------------


def run(coro):  # type: ignore[no-untyped-def]
    return asyncio.run(coro)


def test_check_attempt_runs_in_a_worker_process() -> None:
    result = run(check_attempt(EQUATION, True, "2x = 8\nx = 4"))
    decided(result, "correct")
    assert maths._worker._process is not None
    assert maths._worker._process.pid != multiprocessing.current_process().pid


def test_ac_006_9_timeout_kills_and_respawns_the_worker() -> None:
    run(check_attempt(EQUATION, True, "x = 4"))
    old = maths._worker._process
    assert old is not None
    result = run(check_attempt(EQUATION, True, "2x = 8\nx = 4", timeout_s=0.0))
    undecided(result)
    assert "too long" in result.reason
    old.join(5)
    assert not old.is_alive()
    again = run(check_attempt(EQUATION, True, "2x = 8\nx = 4"))
    decided(again, "correct")
    assert maths._worker._process is not old


def test_worker_killed_between_checks_is_respawned() -> None:
    run(check_attempt(EQUATION, True, "x = 4"))
    process = maths._worker._process
    assert process is not None
    process.kill()
    process.join(5)
    decided(run(check_attempt(EQUATION, True, "x = 4")), "correct")


def test_check_attempt_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_args: object, **_kwargs: object) -> CheckResult:
        raise RuntimeError("boom")

    monkeypatch.setattr(maths._worker, "run", boom)
    undecided(run(check_attempt(EQUATION, True, "x = 4")))
    undecided(run(check_attempt(EQUATION, True, "x" * 1000)))


def test_check_sync_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_args: object, **_kwargs: object) -> CheckResult:
        raise MemoryError

    monkeypatch.setattr(maths, "_check", boom)
    undecided(check_sync(EQUATION, True, "x = 4"))


def _allocate_too_much(connection: Connection) -> None:  # pragma: no cover - child process
    maths._apply_limits(512 * 1024**2)
    try:
        bytearray(1024**3)
    except MemoryError:
        connection.send("MemoryError")
    else:
        connection.send("allocated")


@pytest.mark.skipif(maths.resource is None, reason="resource.setrlimit is Unix-only")
def test_worker_limits_memory() -> None:
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe()
    process = context.Process(target=_allocate_too_much, args=(child,))
    process.start()
    assert parent.poll(60)
    assert parent.recv() == "MemoryError"
    process.join(10)


# API level: the checker's verdict wins (AC-006.8, AC-006.10) --------------------------


def queue_verdict(fake: FakeModel, verdict: str, **fields: object) -> None:
    reply = {
        "verdict": verdict,
        "explanation": "Because.",
        "first_error_step": None,
        "hint": "Try again.",
        **fields,
    }
    fake.structured_replies["Verdict"] = [json.dumps(reply)]


def verdict_event(client: TestClient, text: str) -> dict:  # type: ignore[type-arg]
    got = events(send(client, intent="check", text=text))
    return next(e for e in got if e["type"] == "verdict")


def test_ac_006_8_checker_verdict_overrides_a_contradicting_model(
    client: TestClient, fake: FakeModel
) -> None:
    join(client)
    send(client, text=EQUATION)
    queue_verdict(fake, "correct", first_error_step="x = 7")
    shown = verdict_event(client, "2x = 14\nx = 7")
    assert shown["verdict"] == "not_yet_correct"
    assert shown["first_error_step"] == "2x = 14"
    assert shown["explanation"] == "Because."


def test_ac_006_8_correct_override_clears_first_error_step(
    client: TestClient, fake: FakeModel
) -> None:
    join(client)
    send(client, text=EQUATION)
    queue_verdict(fake, "not_yet_correct", first_error_step="x = 4")
    shown = verdict_event(client, "2x = 8\nx = 4")
    assert shown["verdict"] == "correct"
    assert shown["first_error_step"] is None


def test_ac_006_8_partly_correct_from_checker(client: TestClient, fake: FakeModel) -> None:
    join(client)
    send(client, text=EQUATION)
    queue_verdict(fake, "correct")
    assert verdict_event(client, "2x = 8")["verdict"] == "partly_correct"


def test_ac_006_8_solution_is_not_given_to_the_model_unless_correct(
    client: TestClient, fake: FakeModel
) -> None:
    join(client)
    send(client, text=EQUATION)
    queue_verdict(fake, "correct")
    verdict_event(client, "2x = 14\nx = 7")
    request = str(fake.calls[-1][-1]["content"])
    assert "not_yet_correct" in request
    assert "x = 4" not in request
    assert "= 4" not in request


def test_ac_006_9_undecided_attempt_uses_the_model_verdict(
    client: TestClient, fake: FakeModel
) -> None:
    join(client)
    send(client, text=EQUATION)
    queue_verdict(fake, "partly_correct", first_error_step="step 2")
    shown = verdict_event(client, "I think x is four")
    assert shown["verdict"] == "partly_correct"
    assert shown["first_error_step"] == "step 2"
    assert "deterministic checker" not in str(fake.calls[-1][-1]["content"])


def test_ac_006_10_photo_attempt_uses_the_model_verdict(
    client: TestClient, fake: FakeModel
) -> None:
    join(client)
    send(client, text=EQUATION)
    queue_verdict(fake, "correct")
    got = events(send(client, intent="check", text="x = 7", image=photo_bytes()))
    shown = next(e for e in got if e["type"] == "verdict")
    assert shown["verdict"] == "correct"  # the checker would say not_yet_correct


def test_ac_006_7_photo_exercise_keeps_the_model_for_the_final_answer(
    client: TestClient, fake: FakeModel
) -> None:
    join(client)
    fake.structured_replies["ExerciseAnalysis"] = [analysis()]
    send(client, image=photo_bytes())
    queue_verdict(fake, "partly_correct")
    assert verdict_event(client, "2x = 8\nx = 4")["verdict"] == "partly_correct"
    fake.structured_replies["Verdict"] = [
        json.dumps({"verdict": "correct", "explanation": "ok", "first_error_step": None})
    ]
    shown = verdict_event(client, "2x + 3 = 11\n2x = 9")
    assert shown["verdict"] == "not_yet_correct"
    assert shown["first_error_step"] == "2x = 9"


def test_ac_006_10_other_tutors_keep_the_model_verdict(client: TestClient, fake: FakeModel) -> None:
    join(client, tutor="science")
    send(client, text=EQUATION)
    queue_verdict(fake, "correct")
    got = events(send(client, intent="check", text="2x = 14\nx = 7"))
    assert next(e for e in got if e["type"] == "verdict")["verdict"] == "correct"
