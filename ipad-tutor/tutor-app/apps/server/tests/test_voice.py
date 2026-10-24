"""Phase 9: voice pieces that don't need audio hardware (commands, speakable text, VAD segmentation)."""

import asyncio

import numpy as np
from inktutor_server._voice_commands import next_level
from inktutor_server.voice.session import VoiceSession, parse_command, speakable


def test_spoken_hint_ladder_commands():
    assert parse_command("Just a hint, please.") == "hint"
    assert parse_command("una pista") == "hint"
    assert parse_command("show me the step") == "step"
    assert parse_command("vull la solució") == "solution"
    assert parse_command("why is this wrong?") is None  # a question → the tutor answers it
    assert [next_level("hint", 0), next_level("hint", 2), next_level("more", 2), next_level("step", 1)] == [1, 2, 3, 3]
    assert next_level("solution", 1) == 4  # saying it is an explicit request


def test_speakable_reads_maths_aloud():
    s = speakable(r"If you differentiate $\frac{x^5}{3}$, do you get $x^3$ back?")
    assert "$" not in s and "\\" not in s and "{" not in s
    assert "x to the 5 over 3" in s and "x to the 3" in s
    assert (
        speakable("Differentiate x⁵/3: do you get x³ back?")
        == "Differentiate x to the 5/3: do you get x to the 3 back?"
    )


class FakeEngine:
    tts_rate = 24000

    def __init__(self):
        self.transcribed = []

    def reset_vad(self):
        pass

    def speech_prob(self, frame):
        return 0.9 if np.abs(frame).mean() > 0.05 else 0.05

    async def transcribe(self, audio):
        self.transcribed.append(len(audio))
        return "just a hint"


def test_vad_cuts_one_utterance_after_350_ms_of_silence():
    async def run():
        heard = []
        eng = FakeEngine()

        async def emit(m):
            pass

        async def on_utt(text, pen, t_end):
            heard.append(text)

        vs = VoiceSession(eng, emit, on_utt)
        import base64

        def chunk(x):
            return base64.b64encode((x * 32767).astype("<i2").tobytes()).decode()

        speech = (np.sin(np.arange(16000) / 3) * 0.3).astype(np.float32)  # 1 s "speech"
        for i in range(0, 16000, 1600):
            await vs.feed(chunk(speech[i : i + 1600]), 16000, None)
        assert heard == []  # still talking
        for _ in range(5):  # 500 ms of silence
            await vs.feed(chunk(np.zeros(1600, dtype=np.float32)), 16000, None)
        await asyncio.sleep(0.05)
        return heard, eng.transcribed

    heard, lens = asyncio.run(run())
    assert heard == ["just a hint"]
    assert 16000 <= lens[0] <= 16000 + 512 * 20  # the utterance (+ pre-roll and the closing silence)


def test_streamed_speak_line_yields_each_finished_sentence_early():
    from inktutor_server.tutor.base import JsonLines

    line = '{"type": "speak", "line_id": "L1", "text": "Check the $\\frac{x^5}{3}$ term. Then integrate again."}\n'
    p, out = JsonLines(), []
    for ch in line:  # token by token
        out += [(o["type"], o["text"]) for o in p.feed(ch)]
    assert out == [
        ("speak_partial", "Check the $\\frac{x^5}{3}$ term."),
        ("speak_partial", "Check the $\\frac{x^5}{3}$ term. Then integrate again."),  # the text closed
        ("speak", "Check the $\\frac{x^5}{3}$ term. Then integrate again."),
    ]


def test_guess_lang_picks_the_kokoro_voice_language():
    from inktutor_server.voice.session import guess_lang

    assert guess_lang("Why is this wrong?") == "a"
    assert guess_lang("¿Por qué está mal esto?") == "e"
    assert guess_lang("Per què això està malament?") == "e"  # Catalan → the Spanish voice
    assert guess_lang("Pourquoi est-ce que c'est faux ?") == "f"
    assert guess_lang("x = 3") == "a"


class SpeakingEngine(FakeEngine):
    def __init__(self):
        super().__init__()
        self.spoken = []

    async def speak(self, text, lang="a"):
        self.spoken.append((text, lang))
        await asyncio.sleep(0.01)
        yield np.zeros(240, dtype=np.float32)


def test_converse_speaks_the_first_sentence_before_the_answer_is_complete():
    """The tutor streams; TTS starts on the first finished sentence, the panel gets the whole answer once."""
    from types import SimpleNamespace

    from inktutor_server.agent.agent import Agent

    async def run():
        eng, sent, events = SpeakingEngine(), [], []
        spoken_when_partial_done = []

        async def emit(m):
            events.append(m)

        vs = VoiceSession(eng, emit, None)

        async def ask_tutor(page, kind, ctx):
            yield {"type": "speak_partial", "text": "Look at the exponent."}
            await asyncio.sleep(0.05)  # the model is still writing the rest
            spoken_when_partial_done.append(list(eng.spoken))
            yield {"type": "speak", "line_id": "L1", "text": "Look at the exponent. Differentiate x⁵/3."}
            yield {"type": "speak", "line_id": "L1", "text": "A repeated, longer answer that is never said."}

        async def send(page, ev, **kw):
            sent.append(ev.text)

        line = SimpleNamespace(region=SimpleNamespace(id="L1"))
        stub = SimpleNamespace(
            voice=vs,
            runtime=None,
            mode=SimpleNamespace(name="math", rubric=""),
            session=SimpleNamespace(problem=""),
            _voice_page=lambda pen: (SimpleNamespace(page=SimpleNamespace(id="p1"), lines={"L1": line}), line),
            _line_ctx=lambda ls: None,
            _voice_ctx=lambda page, ls, text: None,
            take_dictation=lambda: None,
            _ask_tutor=ask_tutor,
            _send=send,
            emit=emit,
        )
        stub.mode.name = "math"
        await Agent._on_utterance(stub, "why is this wrong?", None, 0.0)
        return eng.spoken, spoken_when_partial_done, sent

    spoken, early, sent = asyncio.run(run())
    assert early == [[("Look at the exponent.", "a")]]  # already speaking while the tutor writes
    assert spoken == [("Look at the exponent.", "a"), ("Differentiate x to the 5/3.", "a")]
    assert sent == ["Look at the exponent. Differentiate x⁵/3."]


def test_speech_start_pauses_the_review_and_the_turn_end_resumes_it():
    import base64
    from types import SimpleNamespace

    from inktutor_server.agent.agent import Agent

    async def run():
        log = []

        async def emit(m):
            pass

        async def on_utt(text, pen, t_end):
            log.append("answer")

        vs = VoiceSession(
            FakeEngine(),
            emit,
            on_utt,
            on_speech_start=lambda: log.append("pause"),
            on_turn_end=lambda: log.append("resume"),
        )

        def chunk(x):
            return base64.b64encode((x * 32767).astype("<i2").tobytes()).decode()

        await vs.feed(chunk((np.sin(np.arange(8000) / 3) * 0.3).astype(np.float32)), 16000, None)
        await vs.feed(chunk(np.zeros(8000, dtype=np.float32)), 16000, None)
        await asyncio.sleep(0.05)

        # the agent side: a streaming review is cancelled on speech start and scheduled again at turn end
        async def forever():
            await asyncio.sleep(60)

        review = asyncio.create_task(forever())
        page = SimpleNamespace(task=review, settle=None)
        resumed = []

        async def after_settle(p):
            resumed.append(p)

        stub = SimpleNamespace(pages={"p1": page}, reviewing={"p1"}, paused=set(), _after_settle=after_settle)
        Agent._pause_reviews(stub)
        await asyncio.sleep(0)
        cancelled = review.cancelled()
        Agent._resume_reviews(stub)
        await asyncio.sleep(0)
        return log, cancelled, resumed == [page], stub.paused

    log, cancelled, resumed, paused = asyncio.run(run())
    assert log == ["pause", "answer", "resume"]
    assert cancelled and resumed and paused == set()


def test_fillers_are_not_answered():
    from inktutor_server.voice.session import is_filler

    assert all(is_filler(t) for t in ["Uh", "um...", "Hmm.", "uh, um", "OK."])
    assert not any(is_filler(t) for t in ["Uh, why is this wrong?", "a hint", "more"])


def test_a_one_sentence_answer_is_released_when_its_text_closes():
    from inktutor_server.tutor.base import JsonLines

    p, out = JsonLines(), []
    for ch in '{"type":"speak","text":"Check the power rule.","line_id":"L2"}':  # no newline yet
        out += [(o["type"], o["text"]) for o in p.feed(ch)]
    assert out == [("speak_partial", "Check the power rule.")]


def test_converse_prefill_shares_the_real_questions_prefix():
    """Speech start → a 1-token prefill whose prompt is the real request's prompt up to the student's words."""
    import json as _json

    import httpx
    from inktutor_server.tutor.base import LineContext, VoiceContext
    from inktutor_server.tutor.local import LocalMLX, converse_user

    sent = []

    def handler(request):
        sent.append(_json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "{"}}]})

    line = LineContext(id="L2", latex="x^5/3", text=None, kind="math", confidence=0.9, checks=["wrong"])

    def ctx(said):
        return VoiceContext(mode="math", problem="P", rubric="R", transcript=said, pen_line=line, lines=[line])

    tutor = LocalMLX("http://mlx/v1", "served", "gemma4-26b-a4b", transport=httpx.MockTransport(handler))
    asyncio.run(tutor.warm(ctx("")))
    (body,) = sent
    assert body["max_tokens"] == 1 and body["stream"] is False
    warm_user = body["messages"][1]["content"][0]["text"]
    real_user = converse_user(ctx("Why is this wrong?"))[0]["text"]
    cut = warm_user.index('"student_said": "') + len('"student_said": "')
    assert real_user[:cut] == warm_user[:cut]  # everything before the student's words is cached


def test_speech_start_prefills_with_the_local_tutor_only():
    from types import SimpleNamespace

    from inktutor_server.agent.agent import Agent

    async def run(tutor):
        line = SimpleNamespace(region=SimpleNamespace(id="L1"))
        page = SimpleNamespace(page=SimpleNamespace(id="p1"), lines={"L1": line})
        stub = SimpleNamespace(
            tutor=lambda: tutor,
            voice=SimpleNamespace(pen=None),
            _voice_page=lambda pen: (page, line),
            _voice_ctx=lambda page, ls, text: ("ctx", text),
            background=set(),
        )
        stub._warm_done = lambda t: Agent._warm_done(stub, t)
        Agent._warm_converse(stub)
        await asyncio.sleep(0.01)
        return stub.background

    warmed = []

    class Local:
        async def warm(self, ctx):
            warmed.append(ctx)

    class Cloud:  # no warm(): a cloud call would cost money for nothing
        pass

    assert asyncio.run(run(Local())) == set() and warmed == [("ctx", "")]
    asyncio.run(run(Cloud()))
    assert warmed == [("ctx", "")]


def test_dictated_words_fill_the_correction_instead_of_being_answered():
    from types import SimpleNamespace

    from inktutor_server.agent.agent import Agent

    asked = []
    stub = SimpleNamespace(dictating=("p1", "L1"), _voice_page=lambda pen: asked.append(pen) or (None, None))
    stub.take_dictation = lambda: Agent.take_dictation(stub)
    asyncio.run(Agent._on_utterance(stub, "x squared plus three", None, 0.0))
    assert asked == [] and stub.dictating is None  # not answered, and the next question is answered again
    asyncio.run(Agent._on_utterance(stub, "why is this wrong?", None, 0.0))
    assert asked == [None]
