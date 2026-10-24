"""Turn student intents into model calls and stream events back (REQ-003…REQ-007)."""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from .checker.maths import CheckResult, check_attempt
from .model import Message, ModelClient, ModelError
from .sessions import Conversation, Exercise, Turn
from .tutors import Tutor

Intent = Literal["ask", "new_exercise", "hint", "answer", "check", "practice", "mark"]
Event = dict[str, Any]

READ_HANDWRITING = (
    "The image is the student's handwritten answer or working for the exercise. Copy exactly "
    "what is written as plain text, one line per written line, with maths in plain text "
    "(x^2, 3/4, sqrt(9)). Do not correct, complete or solve anything. If you can't read it, "
    "set readable to false."
)
MISTAKES_REMINDER = "AI can make mistakes. If something looks wrong, check with your teacher."

LEVEL_INSTRUCTIONS = {
    1: "Reply at hint level 1 (a nudge). Do not give the final answer.",
    2: "Reply at hint level 2 (name the rule or method). Do not give the final answer.",
    3: "Reply at hint level 3 (the correct next step only). Do not give the final answer.",
    4: "The student asked for the answer: reply at hint level 4 with the full worked solution.",
}
MAX_TOKENS = {"hint": 400, "answer": 900, "structured": 600}


class ExerciseAnalysis(BaseModel):
    readable: bool
    exercise_count: int = Field(ge=0)
    exercise_text: str
    topic: str


class HandwritingReading(BaseModel):
    readable: bool
    text: str


class TopicChoice(BaseModel):
    topic: str


class Verdict(BaseModel):
    verdict: Literal["correct", "partly_correct", "not_yet_correct"]
    explanation: str
    first_error_step: str | None = None
    hint: str | None = None


class PracticeExercise(BaseModel):
    number: int
    text: str


class PracticeSet(BaseModel):
    exercises: list[PracticeExercise] = Field(min_length=3, max_length=3)


class Limits(BaseModel):
    first_output_s: float = 60.0
    call_s: float = 120.0
    turns_kept: int = 20


def system_prompt(core: str, tutor: Tutor) -> str:
    examples = "\n\n".join(f"Student: {e.student}\nTutor: {e.tutor}" for e in tutor.config.examples)
    parts = [
        core,
        f"# Subject tutor: {tutor.config.name}",
        "## teaching\n" + tutor.rubric["teaching"],
        "## checking answers\n" + tutor.rubric["check"],
        "## practice\n" + tutor.rubric["practice"],
    ]
    if examples:
        parts.append("## examples of good replies\n" + examples)
    return "\n\n".join(parts)


_SPOOFED = re.compile(r"</?\s*student\s*>|\[\s*hub instruction\s*\]", re.IGNORECASE)


def student_block(text: str) -> str:
    """Wrap student (or photo) text; tags or hub markers inside it are neutralised so a
    student can't close the block or fake a hub instruction (REQ-010)."""
    safe = _SPOOFED.sub(lambda m: "(" + m.group(0).strip("<>[]/ ") + ")", text)
    return f"<student>\n{safe}\n</student>"


class Tutoring:
    def __init__(
        self,
        model: ModelClient,
        prompts_dir: Path,
        limits: Limits,
        checker_fallback: bool = False,
    ) -> None:
        # checker_fallback: show the maths checker's verdict even if the model can't explain
        # it (the web app); off for `opentutor eval` so model failures are still recorded.
        self.checker_fallback = checker_fallback
        self.model = model
        self.core = (prompts_dir / "core.md").read_text()
        self.limits = limits

    def messages(
        self, tutor: Tutor, convo: Conversation, request: str, image: bytes | None = None
    ) -> list[Message]:
        """System prompt, then the exercise (kept even when old turns are dropped), then turns."""
        msgs: list[Message] = [{"role": "system", "content": system_prompt(self.core, tutor)}]
        if convo.exercise:
            exercise: Message = {
                "role": "user",
                "content": "The exercise:\n" + student_block(convo.exercise.text or "(see photo)"),
            }
            if convo.exercise.image:
                exercise["images"] = [convo.exercise.image]
            msgs.append(exercise)
            msgs.append({"role": "assistant", "content": "Understood."})
        for turn in convo.turns[-self.limits.turns_kept :]:
            role = "user" if turn.role == "student" else "assistant"
            content = student_block(turn.text) if role == "user" else turn.text
            msgs.append({"role": role, "content": content})
        final: Message = {"role": "user", "content": request}
        if image:
            final["images"] = [image]
        msgs.append(final)
        return msgs

    async def _stream(self, msgs: list[Message], max_tokens: int) -> AsyncIterator[str]:
        """Model output with the first-output and total-time limits (AC-003.3)."""
        start = time.monotonic()
        chunks = aiter(self.model.stream(msgs, max_tokens))
        first = True
        while True:
            limit = self.limits.first_output_s if first else self.limits.call_s
            remaining = limit - (time.monotonic() - start)
            try:
                chunk = await asyncio.wait_for(anext(chunks), timeout=max(remaining, 0.01))
            except StopAsyncIteration:
                return
            except TimeoutError as error:
                raise ModelError("timeout") from error
            first = False
            yield chunk

    async def _structured[T: BaseModel](
        self, msgs: list[Message], schema: type[T], max_tokens: int = MAX_TOKENS["structured"]
    ) -> T | None:
        """Ask for JSON matching `schema`; retry once with the validation error (AC-006.4)."""
        deadline = time.monotonic() + self.limits.call_s  # one budget for both attempts
        for _attempt in range(2):
            raw = await asyncio.wait_for(
                self.model.structured(msgs, schema, max_tokens),
                timeout=max(deadline - time.monotonic(), 0.01),
            )
            try:
                return schema.model_validate_json(raw)
            except ValidationError as error:
                msgs = [
                    *msgs,
                    {"role": "assistant", "content": raw},
                    {"role": "user", "content": f"That JSON was invalid: {error}. Reply again."},
                ]
        return None

    async def _tutor_reply(
        self,
        tutor: Tutor,
        convo: Conversation,
        student_text: str,
        level: int,
        image: bytes | None,
        shown: str | None = None,
    ) -> AsyncIterator[Event]:
        """Stream a tutor reply; `shown` is what the conversation records for the student."""
        request = student_block(student_text) if student_text else "(no message)"
        request += "\n\n[Hub instruction] " + LEVEL_INSTRUCTIONS[level]
        max_tokens = MAX_TOKENS["answer" if level == 4 else "hint"]
        reply: list[str] = []
        async for chunk in self._stream(self.messages(tutor, convo, request, image), max_tokens):
            reply.append(chunk)
            yield {"type": "token", "text": chunk}
        convo.turns.append(Turn("student", shown if shown is not None else student_text))
        convo.turns.append(Turn("tutor", "".join(reply).strip()))
        if level <= 3:
            convo.hint_level = (
                level  # only once the reply arrived, so a failure doesn't skip a step
            )
        if level == 4:
            yield {"type": "notice", "code": "mistakes_reminder", "message": MISTAKES_REMINDER}

    async def classify_topic(self, tutor: Tutor, exercise: Exercise) -> str:
        """One of the tutor's fixed topics for a typed exercise, else "other" (AC-013.3)."""
        request = (
            "Which topic is this exercise about? Answer with exactly one of: "
            + ", ".join([*tutor.config.topics, "other"])
            + "\nExercise:\n"
            + student_block(exercise.text)
        )
        msgs: list[Message] = [{"role": "user", "content": request}]
        choice = await self._structured(msgs, TopicChoice)
        topic = choice.topic.strip().lower() if choice else "other"
        return topic if topic in tutor.config.topics else "other"

    async def handle(
        self,
        tutor: Tutor,
        convo: Conversation,
        intent: Intent,
        text: str,
        image: bytes | None,
        practice_answers: bool = False,
        handwriting: bool = False,
        known_lines: list[str | None] | None = None,
    ) -> AsyncIterator[Event]:
        if intent == "ask" and (convo.exercise is None or image is not None):
            intent = "new_exercise"
        if intent == "new_exercise":
            async for event in self._new_exercise(tutor, convo, text, image):
                yield event
        elif intent in ("hint", "answer", "ask"):
            if convo.exercise is None:
                yield _notice("no_exercise", "Send the exercise first: a photo or typed question.")
                return
            if intent == "hint":
                level = min(max(convo.hint_level, 1) + 1, 3)
            else:
                level = 4 if intent == "answer" else max(convo.hint_level, 1)
            async for event in self._tutor_reply(tutor, convo, text, level, image):
                yield event
        elif intent == "check":
            async for event in self._check(
                tutor, convo, text, image, practice_answers, handwriting
            ):
                yield event
        elif intent == "practice":
            async for event in self._practice(tutor, convo):
                yield event
        elif intent == "mark":
            async for event in self._mark(tutor, convo, image, known_lines):
                yield event

    async def _new_exercise(
        self, tutor: Tutor, convo: Conversation, text: str, image: bytes | None
    ) -> AsyncIterator[Event]:
        if image is None:
            convo.turns.clear()
            convo.practice.clear()
            convo.hint_level = 0
            convo.exercise = Exercise(text=text)
            async for event in self._tutor_reply(tutor, convo, "", 1, None, shown=text):
                yield event
            return
        topics = ", ".join(tutor.config.topics)
        request = (
            "Read the photo. Report whether a school exercise is readable, how many separate "
            f"exercises it shows, the exercise text, and the topic (one of: {topics}, other)."
            + (f"\nThe student also wrote:\n{student_block(text)}" if text else "")
        )
        analysis = await self._structured(
            self.messages(tutor, Conversation(tutor.id), request, image), ExerciseAnalysis
        )
        if analysis is None or not analysis.readable or analysis.exercise_count == 0:
            # Keep the previous exercise and conversation: a bad photo shouldn't wipe them.
            yield _notice(
                "unreadable_image",
                "I can't read an exercise in this photo. Try again with the page flat, in good "
                "light, and one exercise in the frame — or type the exercise.",
            )
            return
        topic = analysis.topic if analysis.topic in tutor.config.topics else "other"
        convo.turns.clear()
        convo.practice.clear()
        convo.hint_level = 0
        convo.exercise = Exercise(
            text=analysis.exercise_text, image=image, topic=topic, typed=False
        )
        if analysis.exercise_count > 1:
            yield _notice(
                "several_exercises",
                "I can see several exercises in this photo. Which one do you want help with?",
            )
            return
        shown = f"(photo) {text}".strip()
        async for event in self._tutor_reply(tutor, convo, text, 1, None, shown=shown):
            yield event

    async def _check(
        self,
        tutor: Tutor,
        convo: Conversation,
        text: str,
        image: bytes | None,
        practice_answers: bool = False,
        handwriting: bool = False,
    ) -> AsyncIterator[Event]:
        if convo.exercise is None:
            yield _notice("no_exercise", "Which exercise is this answer for? Send it first.")
            return
        if image is not None and handwriting:
            # Read -> check -> explain (REQ-019, the idea of ipad-tutor/): the model only reads
            # the writing; the student sees what was read, and the text is checked as typed.
            reading = await self._structured(
                self.messages(tutor, convo, READ_HANDWRITING, image), HandwritingReading
            )
            if reading is None or not reading.readable or not reading.text.strip():
                yield _notice(
                    "unreadable_handwriting",
                    "I can't read that writing. Try writing bigger, or type your answer.",
                )
                return
            yield {"type": "reading", "text": reading.text.strip()}
            text, image = reading.text.strip(), None
        # Only answers sent from the practice panel count as practice answers, so a student's
        # own numbered working ("1. 2x = 8") is still checked against the main exercise.
        answers = _practice_answers(text, convo.practice) if practice_answers and not image else {}
        if answers:
            # One verdict per practice answer, each checked against its own exercise (AC-007.2).
            main = convo.exercise
            try:
                for number, answer in answers.items():
                    convo.exercise = Exercise(
                        text=convo.practice[number - 1], typed=True, topic=main.topic
                    )
                    verdict = await self._check_one(tutor, convo, answer, None)
                    if verdict is None:
                        yield _notice("check_failed", f"I couldn't check answer {number}.")
                        continue
                    yield {"type": "verdict", "practice_number": number, **verdict.model_dump()}
            finally:
                convo.exercise = main
            return
        verdict = await self._check_one(tutor, convo, text, image)
        if verdict is None:
            yield _notice("check_failed", "I couldn't check that. Try writing your answer again.")
            return
        yield {"type": "verdict", **verdict.model_dump()}

    async def _check_one(
        self, tutor: Tutor, convo: Conversation, text: str, image: bytes | None
    ) -> Verdict | None:
        """Verdict for one attempt at the current exercise; the maths checker wins if it decides."""
        decision = await self._maths_decision(tutor, convo, text, image)
        request = (
            "Check the student's attempt at the exercise. Give a verdict, a short explanation, "
            "the first incorrect step if any, and a hint to fix it. Never state the final answer "
            "unless the verdict is correct.\nAttempt:\n" + student_block(text or "(see photo)")
        )
        if decision:
            request = _explain_request(decision, text)
        try:
            verdict = await self._structured(self.messages(tutor, convo, request, image), Verdict)
        except (ModelError, TimeoutError):
            if not (decision and self.checker_fallback):
                raise
            verdict = None
        if verdict is None and decision and decision.verdict and self.checker_fallback:
            # The checker already knows; don't hide its verdict because the explanation failed.
            verdict = Verdict(
                verdict=decision.verdict, explanation=_FALLBACK_EXPLANATION[decision.verdict]
            )
        if verdict is None:
            return None
        if decision and decision.verdict:
            # The checker's verdict always wins; the model only explains it (AC-006.8).
            verdict.verdict = decision.verdict
            verdict.first_error_step = decision.first_error_step
        convo.turns.append(Turn("student", f"My answer: {text or '(photo)'}"))
        lines = [verdict.explanation]
        if verdict.first_error_step:
            lines.append(f"First step to fix: {verdict.first_error_step}")
        if verdict.hint:
            lines.append(f"Hint: {verdict.hint}")
        convo.turns.append(Turn("tutor", "\n".join(lines), verdict=verdict.verdict))
        return verdict

    async def _maths_decision(
        self, tutor: Tutor, convo: Conversation, text: str, image: bytes | None
    ) -> CheckResult | None:
        """The maths checker's decision for a typed attempt, else None (AC-006.9, AC-006.10)."""
        if tutor.config.checker != "maths" or image is not None or convo.exercise is None:
            return None
        result = await check_attempt(convo.exercise.text, convo.exercise.typed, text)
        return result if result.decided and result.verdict else None

    async def _mark(
        self,
        tutor: Tutor,
        convo: Conversation,
        image: bytes | None,
        known_lines: list[str | None] | None,
    ) -> AsyncIterator[Event]:
        """Live marks for the writing pad (AC-019.6): read the pad, mark each line, no chat turn.

        As in ipad-tutor, the pad sends one slot per written line: the text of lines it has
        already had read, and None for new or changed lines, whose crops come stacked in
        `image`. Only those are read, with a short prompt and no conversation, so a mark
        costs at most one small model call, and none after an undo. The deterministic
        checker owns the marks.
        """
        if convo.exercise is None:
            yield _notice("no_exercise", "Send the exercise first, then write your working.")
            return
        slots: list[str | None] = [None] if known_lines is None else known_lines
        unread = slots.count(None)
        if unread and image is None:
            yield _notice("no_writing", "Write something on the pad first.")
            return
        readable = True
        lines = [slot or "" for slot in slots]
        if unread:
            reading = await self._structured(
                _reading_messages(image), HandwritingReading, _reading_tokens(unread)
            )
            read = [line.strip() for line in (reading.text if reading else "").splitlines()]
            read = [line for line in read if line]
            readable = bool(reading and reading.readable and read)
            if known_lines is None:
                lines = read if readable else []
            else:
                lines = _fill_slots(slots, read if readable else [])
        written = [line for line in lines if line]
        decision = None
        if written and tutor.config.checker == "maths":
            decision = await check_attempt(
                convo.exercise.text, convo.exercise.typed, "\n".join(written)
            )
        marks = iter(_line_marks(written, decision))
        verdict = decision.verdict if decision and decision.decided else None
        yield {
            "type": "marks",
            "readable": readable,
            "lines": [next(marks) if line else {"text": "", "mark": "unknown"} for line in lines],
            "verdict": verdict,
        }

    async def _practice(self, tutor: Tutor, convo: Conversation) -> AsyncIterator[Event]:
        if convo.exercise is None:
            yield _notice("no_exercise", "Which topic do you want to practise? Send an exercise.")
            return
        request = (
            "Write exactly 3 new exercises on the same topic and at a similar difficulty as the "
            "exercise, numbered 1 to 3, without answers."
        )
        practice = await self._structured(self.messages(tutor, convo, request), PracticeSet)
        if practice is None:
            yield _notice(
                "practice_failed", "Practice isn't available for this exercise right now."
            )
            return
        # Renumber 1..3 ourselves: the model's numbers can't be trusted to match.
        convo.practice = [e.text for e in sorted(practice.exercises, key=lambda e: e.number)]
        listing = "\n".join(f"{i}. {text}" for i, text in enumerate(convo.practice, start=1))
        convo.turns.append(Turn("tutor", "Practice exercises:\n" + listing))
        numbered = [{"number": i, "text": text} for i, text in enumerate(convo.practice, start=1)]
        yield {"type": "practice", "exercises": numbered}


def _explain_request(decision: CheckResult, attempt: str) -> str:
    """Ask the model to explain a verdict it must not change.

    The checker's solution is never included; a "correct" verdict needs none because the
    student's last step already is the answer (AC-006.8).
    """
    if decision.verdict == "correct":
        facts = "The checker confirmed the student's final answer is correct."
    elif decision.verdict == "partly_correct":
        facts = (
            "The checker found every step correct, but the student has not reached the final "
            "answer yet. Encourage the next step without stating the answer."
        )
    else:
        facts = (
            "The checker found that this step is not correct: "
            f"{decision.first_error_step!r}. Explain what to re-check there with a hint, "
            "without writing the correct step or the final answer."
        )
    return (
        f"A deterministic checker has already decided the verdict: {decision.verdict}. {facts} "
        "Write a short explanation and a hint that agree with this verdict; set the verdict "
        'field to "' + str(decision.verdict) + '".\nAttempt:\n' + student_block(attempt)
    )


_FALLBACK_EXPLANATION = {
    "correct": "That's right. Well done!",
    "partly_correct": "Your steps are right so far. Keep going to reach the answer.",
    "not_yet_correct": "Look again at the step shown below. Something went wrong there.",
}


def _squash(value: str) -> str:
    return re.sub(r"\s+", "", value).lower()


def _reading_messages(image: bytes | None) -> list[Message]:
    """Just the reading prompt and the crops: no tutor prompt or history to slow it down."""
    return [
        {"role": "system", "content": READ_HANDWRITING},
        {"role": "user", "content": "Copy the writing in the image.", "images": [image]},
    ]


def _reading_tokens(lines: int) -> int:
    return min(MAX_TOKENS["structured"], 40 + 60 * lines)


def _fill_slots(slots: list[str | None], read: list[str]) -> list[str]:
    """Put the read lines into the unread slots, top to bottom.

    A crop the model reads as more lines than were sent goes to the last unread slot, so a
    tall line or a fraction is not lost; fewer lines leave the remaining slots empty.
    """
    unread = slots.count(None)
    if len(read) > unread:
        read = [*read[: unread - 1], " ".join(read[unread - 1 :])]
    texts = iter(read)
    return [slot if slot is not None else next(texts, "") for slot in slots]


def _line_marks(lines: list[str], decision: CheckResult | None) -> list[dict[str, str]]:
    """One mark per read line: correct / wrong / unknown, from the checker's decision."""
    marks = ["unknown"] * len(lines)
    if decision and decision.decided and decision.verdict in ("correct", "partly_correct"):
        marks = ["correct"] * len(lines)
    elif decision and decision.decided and decision.first_error_step:
        wrong = _squash(decision.first_error_step)
        index = next((i for i, line in enumerate(lines) if wrong and wrong in _squash(line)), None)
        if index is not None:
            marks = ["correct"] * index + ["wrong"] + ["unknown"] * (len(lines) - index - 1)
    return [{"text": line, "mark": mark} for line, mark in zip(lines, marks, strict=True)]


def _practice_answers(text: str, practice: list[str]) -> dict[int, str]:
    """Numbered answers ("1. x = 4", "2) 7") to the current practice exercises."""
    answers: dict[int, str] = {}
    for match in re.finditer(r"^\s*([1-9])\s*[.):]\s*(.+?)\s*$", text, re.M):
        number = int(match.group(1))
        if number <= len(practice):
            answers[number] = match.group(2)
    return answers


def _notice(code: str, message: str) -> Event:
    return {"type": "notice", "code": code, "message": message}
