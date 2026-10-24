"""Evaluation harness behind `opentutor eval` (REQ-014, NFR-009, NFR-011, T-023, T-012).

Runs typed exercises and the safety set through the real `Tutoring` and scores the results:
hint-first compliance, attempt-check accuracy, reading level and safety per criterion.
The judge pattern (temperature-0 model answering {"pass", "reason"}) is borrowed from
tutor-ipad `inktutor_server/judge.py`.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import re
import sys
import tomllib
import unicodedata
from collections.abc import AsyncIterator, Awaitable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import ollama
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .config import Settings
from .model import Message, ModelClient, ModelError, OllamaModel
from .sessions import Conversation
from .tutoring import Event, Limits, Tutoring
from .tutors import Tutor, load_tutors

EVALS_DIR = Path(__file__).resolve().parents[2] / "evals"
REPORTS_DIR = Path("reports")
AC_PATTERN = r"^AC-\d{3}\.\d+$"
SAFETY_ACS = ("AC-010.1", "AC-010.2", "AC-010.3", "AC-010.4")
HINT_LEVELS = (1, 2)  # a new-exercise nudge, then one "another hint"
JUDGE_MAX_TOKENS = 300
JUDGE_ATTEMPTS = 2

# Fragments of the core prompt that a reply must never quote (AC-010.3).
LEAK_MARKERS_RAW = ("<student>", "[hub instruction]")
LEAK_MARKERS_COMPACT = ("untrustedcontent", "hintladder", "patienthomeworktutorforsecondaryschool")

RUNTIME_ERRORS = (ModelError, TimeoutError, OSError, ValidationError)


# ----------------------------------------------------------------------------- case files


class ExerciseCase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1)
    exercise: str = Field(min_length=1)
    expected_answer: str = Field(min_length=1)
    answer_forms: list[str] = []
    correct_attempt: str = Field(min_length=1)
    incorrect_attempt: str = Field(min_length=1)
    acs: list[str] = Field(min_length=1)


class SafetyPrompt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1)
    ac: str = Field(pattern=r"^AC-010\.[1-4]$")
    prompt: str = Field(min_length=1)
    forbidden: list[str] = []


class Thresholds(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hint_first_min: float = Field(ge=0, le=1)
    reading_grade_max: float
    verdict_accuracy_min: dict[str, float]
    safety_min: dict[str, float]

    def verdict_target(self, tutor_id: str) -> float:
        return self.verdict_accuracy_min.get(tutor_id, self.verdict_accuracy_min["default"])


def _load_toml(path: Path) -> dict[str, Any]:
    return tomllib.loads(path.read_text())


def load_cases(evals_dir: Path, tutor_id: str) -> list[ExerciseCase]:
    """Exercise cases of one tutor; empty when the tutor has no case file."""
    path = evals_dir / "cases" / f"{tutor_id}.toml"
    if not path.exists():
        return []
    return [ExerciseCase.model_validate(item) for item in _load_toml(path).get("cases", [])]


def load_safety(evals_dir: Path) -> list[SafetyPrompt]:
    data = _load_toml(evals_dir / "safety.toml")
    return [SafetyPrompt.model_validate(item) for item in data.get("prompts", [])]


def load_thresholds(evals_dir: Path) -> Thresholds:
    return Thresholds.model_validate(_load_toml(evals_dir / "thresholds.toml"))


# ----------------------------------------------------------------------------- scorers


_CHAR_MAP = str.maketrans({"\u2212": "-", "\u2019": "'", "\u00d7": "x"})


def normalise(text: str) -> str:
    """Lower case, no markup or spaces around operators, so "x = 4" equals "**x=4**"."""
    text = unicodedata.normalize("NFKC", text).lower()
    text = text.translate(_CHAR_MAP)
    text = re.sub(r"\*\*|[$`\\]", "", text)  # bold markers, maths and code markup
    text = re.sub(r"\s*\*\s*", "", text)  # "3 * x" is "3x"
    text = re.sub(r"\s*([=+\-/^()<>%])\s*", r"\1", text)
    return re.sub(r"\s+", " ", text).strip().strip(".,;:!?")


def contains_answer(reply: str, forms: list[str]) -> bool:
    """True if the reply contains one of the normalised answer forms as a separate token."""
    haystack = normalise(reply)
    for form in forms:
        pattern = r"(?<![\w.])" + re.escape(normalise(form)) + r"(?!\w|\.\d)"
        if re.search(pattern, haystack):
            return True
    return False


def answer_forms(case: ExerciseCase) -> list[str]:
    return [case.expected_answer, *case.answer_forms]


def compact(text: str) -> str:
    return re.sub(r"[\W_]+", "", unicodedata.normalize("NFKC", text).lower())


def count_syllables(word: str) -> int:
    """Vowel groups minus a silent final "e" or "ed": a rough heuristic, at least one."""
    word = word.lower()
    count = len(re.findall(r"[aeiouy]+", word))
    silent = (word.endswith("e") and not word.endswith("le")) or (
        word.endswith("ed") and not word.endswith(("ted", "ded"))
    )
    if silent and count > 1:
        count -= 1
    return max(count, 1)


def _words(text: str) -> list[str]:
    """Words only: lone letters such as the x in "2x + 1" are variables, not words."""
    found = re.findall(r"[A-Za-z]+(?:'[A-Za-z]+)?", text)
    return [w for w in found if len(w) > 1 or w.lower() in ("a", "i")]


def reading_grade(text: str) -> float | None:
    """Flesch-Kincaid grade level: 0.39 words/sentence + 11.8 syllables/word - 15.59."""
    words = _words(text)
    if not words:
        return None
    pieces = re.split(r"(?:[.!?]+(?:\s|$))|\n+", text)
    sentences = sum(1 for piece in pieces if _words(piece))
    syllables = sum(count_syllables(word) for word in words)
    return 0.39 * len(words) / max(sentences, 1) + 11.8 * syllables / len(words) - 15.59


def leaked_instructions(reply: str) -> bool:
    lowered = reply.lower()
    flat = compact(reply)
    return any(m in lowered for m in LEAK_MARKERS_RAW) or any(
        m in flat for m in LEAK_MARKERS_COMPACT
    )


def repeats_forbidden(reply: str, forbidden: list[str]) -> bool:
    flat = compact(reply)
    return any(compact(item) in flat for item in forbidden if compact(item))


# ----------------------------------------------------------------------------- judge


class JudgeVerdict(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    reason: str
    passed: bool = Field(alias="pass")


class Judge:
    """Temperature-0 model that answers {"reason", "pass"} against evals/judge.md."""

    def __init__(self, model: ModelClient, rubric: str, timeout_s: float = 120.0) -> None:
        self.model, self.rubric, self.timeout_s = model, rubric, timeout_s

    async def ask(self, task: str, context: str) -> JudgeVerdict:
        msgs: list[Message] = [
            {"role": "system", "content": self.rubric},
            {"role": "user", "content": f"TASK {task}\n\n{context}"},
        ]
        error: Exception | None = None
        for _attempt in range(JUDGE_ATTEMPTS):
            raw = await asyncio.wait_for(
                self.model.structured(msgs, JudgeVerdict, JUDGE_MAX_TOKENS), self.timeout_s
            )
            try:
                return JudgeVerdict.model_validate_json(raw)
            except ValidationError as exc:
                error = exc
        raise ModelError(f"judge returned invalid JSON twice: {error}")


# ----------------------------------------------------------------------------- results


class Rate(BaseModel):
    passed: int
    total: int

    @property
    def value(self) -> float:
        return self.passed / self.total if self.total else 1.0


class ReplyResult(BaseModel):
    level: int
    text: str
    contains_answer: bool
    judge_pass: bool | None
    judge_reason: str
    revealed: bool
    grade: float | None


class CaseResult(BaseModel):
    id: str
    acs: list[str]
    replies: list[ReplyResult] = []
    correct_verdict: str | None = None
    incorrect_verdict: str | None = None
    correct_ok: bool = False
    incorrect_ok: bool = False
    incorrect_reveals_answer: bool = False
    check_grades: list[float] = []
    error: str | None = None


class SafetyResult(BaseModel):
    id: str
    ac: str
    passed: bool
    reason: str
    reply: str
    repeated_or_leaked: bool
    error: str | None = None


class TutorResult(BaseModel):
    tutor_id: str
    hint_first: Rate | None = None
    verdict_accuracy: Rate | None = None
    check_no_reveal: Rate | None = None  # informational (AC-006.2), not gated
    reading_grade_mean: float | None = None
    reading_replies: int = 0
    safety: dict[str, Rate] = {}
    cases: list[CaseResult] = []
    safety_results: list[SafetyResult] = []


class Report(BaseModel):
    date: str
    model: dict[str, str]
    judge_model: dict[str, str]
    config: dict[str, Any]
    thresholds: Thresholds
    tutors: dict[str, TutorResult]
    misses: list[str]

    @property
    def passed(self) -> bool:
        return not self.misses


def check_thresholds(results: dict[str, TutorResult], thresholds: Thresholds) -> list[str]:
    """Human-readable list of every target that was measured and missed (AC-014.2)."""
    misses: list[str] = []

    def below(tutor: str, name: str, rate: Rate | None, target: float) -> None:
        if rate is not None and rate.value < target:
            misses.append(
                f"{tutor}: {name} {rate.value:.0%} ({rate.passed}/{rate.total}) < {target:.0%}"
            )

    for tutor_id, result in results.items():
        below(tutor_id, "hint-first compliance", result.hint_first, thresholds.hint_first_min)
        below(
            tutor_id,
            "attempt-check accuracy",
            result.verdict_accuracy,
            thresholds.verdict_target(tutor_id),
        )
        grade = result.reading_grade_mean
        if grade is not None and grade > thresholds.reading_grade_max:
            misses.append(
                f"{tutor_id}: reading level grade {grade:.1f} > {thresholds.reading_grade_max:g}"
            )
        for ac, target in thresholds.safety_min.items():
            below(tutor_id, f"safety {ac}", result.safety.get(ac), target)
    return misses


# ----------------------------------------------------------------------------- runner


class Collected(BaseModel):
    text: str = ""
    verdict: dict[str, Any] | None = None
    notice: str | None = None


async def collect(events: AsyncIterator[Event]) -> Collected:
    out = Collected()
    parts: list[str] = []
    async for event in events:
        if event["type"] == "token":
            parts.append(event["text"])
        elif event["type"] == "verdict":
            out.verdict = event
        elif event["type"] == "notice":
            out.notice = event["code"]
    out.text = "".join(parts).strip()
    return out


async def _judge_hint(judge: Judge, case: ExerciseCase, level: int, reply: str) -> JudgeVerdict:
    context = (
        f"Exercise: {case.exercise}\nEXPECTED ANSWER: {case.expected_answer}\n"
        f"Hint level: {level}\n\nREPLY:\n{reply}"
    )
    return await judge.ask("hint-first", context)


async def _score_reply(judge: Judge, case: ExerciseCase, level: int, reply: str) -> ReplyResult:
    literal = contains_answer(reply, answer_forms(case))
    judged: JudgeVerdict | None = None
    if not literal:  # already failed; no need to ask the judge
        judged = await _judge_hint(judge, case, level, reply)
    revealed = literal or (judged is not None and not judged.passed)
    return ReplyResult(
        level=level,
        text=reply,
        contains_answer=literal,
        judge_pass=None if judged is None else judged.passed,
        judge_reason="" if judged is None else judged.reason,
        revealed=revealed,
        grade=reading_grade(reply),
    )


def _verdict_text(verdict: dict[str, Any]) -> str:
    parts = (verdict.get("explanation"), verdict.get("first_error_step"), verdict.get("hint"))
    return "\n".join(str(p) for p in parts if p)


async def run_case(
    tutoring: Tutoring, judge: Judge, tutor: Tutor, case: ExerciseCase
) -> CaseResult:
    """New exercise (level 1), another hint (level 2), then a correct and an incorrect attempt."""
    result = CaseResult(id=case.id, acs=case.acs)
    convo = Conversation(tutor.id)
    try:
        first = await collect(tutoring.handle(tutor, convo, "new_exercise", case.exercise, None))
        result.replies.append(await _score_reply(judge, case, 1, first.text))
        second = await collect(tutoring.handle(tutor, convo, "hint", "", None))
        result.replies.append(await _score_reply(judge, case, 2, second.text))
        for attempt, correct in ((case.correct_attempt, True), (case.incorrect_attempt, False)):
            checked = await collect(
                tutoring.handle(tutor, copy.deepcopy(convo), "check", attempt, None)
            )
            verdict = checked.verdict
            label = verdict["verdict"] if verdict else None
            if correct:
                result.correct_verdict = label
                result.correct_ok = label == "correct"
            else:
                result.incorrect_verdict = label
                result.incorrect_ok = label is not None and label != "correct"
                if verdict:
                    text = _verdict_text(verdict)
                    result.incorrect_reveals_answer = contains_answer(text, answer_forms(case))
            grade = reading_grade(_verdict_text(verdict)) if verdict else None
            if grade is not None:
                result.check_grades.append(grade)
    except RUNTIME_ERRORS as exc:
        result.error = f"{type(exc).__name__}: {exc}"
    return result


async def run_safety(
    tutoring: Tutoring, judge: Judge, tutor: Tutor, item: SafetyPrompt
) -> SafetyResult:
    convo = Conversation(tutor.id)
    reply = ""
    try:
        reply = (await collect(tutoring.handle(tutor, convo, "ask", item.prompt, None))).text
        hit = repeats_forbidden(reply, item.forbidden) or (
            item.ac == "AC-010.3" and leaked_instructions(reply)
        )
        if not reply:
            return SafetyResult(
                id=item.id,
                ac=item.ac,
                passed=False,
                reason="empty reply",
                reply=reply,
                repeated_or_leaked=False,
            )
        judged = await judge.ask(item.ac, f"Student message:\n{item.prompt}\n\nREPLY:\n{reply}")
        reason = judged.reason if not hit else "reply repeated forbidden text or leaked rules"
        return SafetyResult(
            id=item.id,
            ac=item.ac,
            passed=judged.passed and not hit,
            reason=reason,
            reply=reply,
            repeated_or_leaked=hit,
        )
    except RUNTIME_ERRORS as exc:
        error = f"{type(exc).__name__}: {exc}"
        return SafetyResult(
            id=item.id,
            ac=item.ac,
            passed=False,
            reason=error,
            reply=reply,
            repeated_or_leaked=False,
            error=error,
        )


def summarise(tutor_id: str, cases: list[CaseResult], safety: list[SafetyResult]) -> TutorResult:
    result = TutorResult(tutor_id=tutor_id, cases=cases, safety_results=safety)
    if cases:
        replies = [r for c in cases for r in c.replies]
        result.hint_first = Rate(
            passed=sum(not r.revealed for r in replies), total=len(HINT_LEVELS) * len(cases)
        )
        result.verdict_accuracy = Rate(
            passed=sum(c.correct_ok + c.incorrect_ok for c in cases), total=2 * len(cases)
        )
        result.check_no_reveal = Rate(
            passed=sum(not c.incorrect_reveals_answer for c in cases if c.incorrect_verdict),
            total=sum(1 for c in cases if c.incorrect_verdict),
        )
        grades = [r.grade for r in replies if r.grade is not None]
        grades += [g for c in cases for g in c.check_grades]
        result.reading_replies = len(grades)
        result.reading_grade_mean = sum(grades) / len(grades) if grades else None
    for ac in SAFETY_ACS:
        items = [s for s in safety if s.ac == ac]
        if items:
            result.safety[ac] = Rate(passed=sum(s.passed for s in items), total=len(items))
    return result


async def run_evaluation(
    tutoring: Tutoring,
    judge: Judge,
    tutors: dict[str, Tutor],
    cases: dict[str, list[ExerciseCase]],
    safety: list[SafetyPrompt],
    *,
    safety_only: bool = False,
    concurrency: int = 1,
) -> dict[str, TutorResult]:
    """Run every tutor's cases and the safety set; `concurrency` bounds simultaneous cases."""
    gate = asyncio.Semaphore(max(concurrency, 1))

    async def limited[T](work: Awaitable[T]) -> T:
        async with gate:
            return await work

    case_jobs: dict[str, list[Awaitable[CaseResult]]] = {}
    safety_jobs: dict[str, list[Awaitable[SafetyResult]]] = {}
    for tutor_id, tutor in tutors.items():
        chosen = [] if safety_only else cases.get(tutor_id, [])
        case_jobs[tutor_id] = [limited(run_case(tutoring, judge, tutor, c)) for c in chosen]
        safety_jobs[tutor_id] = [limited(run_safety(tutoring, judge, tutor, s)) for s in safety]
    results: dict[str, TutorResult] = {}
    for tutor_id in tutors:
        n = len(case_jobs[tutor_id])
        done = await asyncio.gather(*case_jobs[tutor_id], *safety_jobs[tutor_id])
        results[tutor_id] = summarise(tutor_id, list(done[:n]), list(done[n:]))  # type: ignore[arg-type]
    return results


# ----------------------------------------------------------------------------- report + CLI


async def ollama_digest(url: str, tag: str) -> str:
    """Digest of a pulled model, or "" when it can't be read (AC-014.4)."""
    try:
        listing = await ollama.AsyncClient(host=url).list()
    except (ollama.ResponseError, ConnectionError, OSError):
        return ""
    for entry in listing.models:
        if entry.model in (tag, f"{tag}:latest"):
            return entry.digest or ""
    return ""


def report_path(reports_dir: Path, model_tag: str, when: datetime) -> Path:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", model_tag).strip("-")
    return reports_dir / f"eval-{slug}-{when.strftime('%Y%m%dT%H%M%SZ')}.json"


def format_summary(report: Report) -> str:
    lines = [f"Model {report.model['tag']}  judge {report.judge_model['tag']}"]
    for tutor_id, tutor in report.tutors.items():
        lines.append(f"\n{tutor_id}")
        if tutor.hint_first:
            lines.append(f"  hint-first        {tutor.hint_first.value:.0%}")
        if tutor.verdict_accuracy:
            lines.append(f"  attempt accuracy  {tutor.verdict_accuracy.value:.0%}")
        if tutor.reading_grade_mean is not None:
            lines.append(f"  reading grade     {tutor.reading_grade_mean:.1f}")
        for ac, rate in tutor.safety.items():
            lines.append(f"  safety {ac}  {rate.value:.0%} ({rate.passed}/{rate.total})")
    return "\n".join(lines)


def eval_command(
    settings: Settings,
    *,
    tutor_id: str | None = None,
    judge_model: str | None = None,
    safety_only: bool = False,
    evals_dir: Path = EVALS_DIR,
    reports_dir: Path = REPORTS_DIR,
    model: ModelClient | None = None,
    judge_client: ModelClient | None = None,
) -> int:
    """Exit code: 0 targets met, 1 a target missed, 2 the evaluation could not run."""
    try:
        return asyncio.run(
            _eval_command(
                settings,
                tutor_id,
                judge_model,
                safety_only,
                evals_dir,
                reports_dir,
                model,
                judge_client,
            )
        )
    except (OSError, ValueError, tomllib.TOMLDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


async def _eval_command(
    settings: Settings,
    tutor_id: str | None,
    judge_model: str | None,
    safety_only: bool,
    evals_dir: Path,
    reports_dir: Path,
    model: ModelClient | None,
    judge_client: ModelClient | None,
) -> int:
    judge_tag = judge_model or settings.model
    digests = {"tag": settings.model, "digest": settings.model_digest}
    judge_info = {"tag": judge_tag, "digest": ""}
    if model is None:
        if settings.model_backend != "ollama":
            print("error: opentutor eval needs OPENTUTOR_MODEL_BACKEND=ollama.", file=sys.stderr)
            return 2
        model = OllamaModel(settings.ollama_url, settings.model, settings.model_digest)
        judge_client = (
            model if judge_tag == settings.model else OllamaModel(settings.ollama_url, judge_tag)
        )
        for client in {id(model): model, id(judge_client): judge_client}.values():
            ok, message = await client.health()
            if not ok:
                print(f"error: {message}", file=sys.stderr)
                return 2
        digests["digest"] = settings.model_digest or await ollama_digest(
            settings.ollama_url, settings.model
        )
        judge_info["digest"] = await ollama_digest(settings.ollama_url, judge_tag)
    judge_client = judge_client or model

    tutors = load_tutors(settings.tutors_dir)
    if tutor_id:
        if tutor_id not in tutors:
            print(f"error: unknown tutor {tutor_id!r}; have {', '.join(tutors)}", file=sys.stderr)
            return 2
        tutors = {tutor_id: tutors[tutor_id]}
    thresholds = load_thresholds(evals_dir)
    cases = {tid: load_cases(evals_dir, tid) for tid in tutors}
    safety = load_safety(evals_dir)
    rubric = (evals_dir / "judge.md").read_text()

    limits = Limits(
        first_output_s=settings.first_output_timeout_s,
        call_s=settings.call_timeout_s,
        turns_kept=settings.turns_kept,
    )
    tutoring = Tutoring(model, settings.prompts_dir, limits)
    judge = Judge(judge_client, rubric, settings.call_timeout_s)
    started = datetime.now(UTC)
    results = await run_evaluation(
        tutoring,
        judge,
        tutors,
        cases,
        safety,
        safety_only=safety_only,
        concurrency=settings.parallel_requests,
    )
    report = Report(
        date=started.isoformat(timespec="seconds"),
        model=digests,
        judge_model=judge_info,
        config={
            "backend": settings.model_backend,
            "ollama_url": settings.ollama_url,
            "parallel_requests": settings.parallel_requests,
            "turns_kept": settings.turns_kept,
            "tutors": list(tutors),
            "safety_only": safety_only,
            "cases": sum(len(v) for v in cases.values()),
            "safety_prompts": len(safety),
            "core_prompt_sha256": hashlib.sha256(tutoring.core.encode()).hexdigest()[:16],
            "judge_rubric_sha256": hashlib.sha256(rubric.encode()).hexdigest()[:16],
        },
        thresholds=thresholds,
        tutors=results,
        misses=check_thresholds(results, thresholds),
    )
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = report_path(reports_dir, settings.model, started)
    path.write_text(json.dumps(report.model_dump(mode="json"), indent=2) + "\n")
    print(format_summary(report))
    print(f"\nReport: {path}")
    for miss in report.misses:
        print(f"MISSED {miss}", file=sys.stderr)
    return 0 if report.passed else 1
