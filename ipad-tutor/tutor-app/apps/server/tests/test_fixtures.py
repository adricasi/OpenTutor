from inktutor_server.fixtures import list_fixtures, load, page_strokes_at, replay_timed


def fixture(stem: str):
    return load(next(p for p in list_fixtures() if p.stem == stem))


def test_all_fixtures_load():
    names = {p.stem for p in list_fixtures()}
    # the photo recording (a picture of the real paper) is kept local, out of the repository
    assert {"rec-math-math-22r_69", "rec-math-consulting-case-z1b_a2"} <= names
    for p in list_fixtures():
        assert load(p).schema_ == "inktutor.recording/1"


def test_replay_reaches_the_recorded_final_page():
    for p in list_fixtures():
        rec = load(p)
        for page in rec.pages:
            assert {s.id for s in page_strokes_at(rec, 1e12, page.id)} == {s.id for s in page.strokes}


def test_intermediate_page_before_the_page_was_wiped():
    rec = fixture("rec-math-math-22r_69")
    assert len(page_strokes_at(rec, 89_900)) == 96  # both integrals on the page
    assert len(page_strokes_at(rec, 116_000)) == 9  # wiped, division started
    assert len(page_strokes_at(rec, 1e12)) == 37


def test_batches_follow_pen_up_debounce():
    rec = fixture("rec-math-math-22r_69")
    msgs = list(replay_timed(rec, debounce_ms=700))
    times = [t for t, _ in msgs]
    assert times == sorted(times)
    assert msgs[0][1].page is not None and all(m.page is None for _, m in msgs[1:])
    # a continuous run of writing is batched: far fewer updates than strokes
    assert 5 < len(msgs) < 135
    # erasing the page produces removals
    assert sum(len(m.strokes_removed) for _, m in msgs) >= 96 - 9
    # no batch is sent while the pen is down: every send time is ≥ 700 ms after a stroke end
    ends = sorted(float(e["t"]) for e in rec.events if e["type"] in ("stroke.end", "erase", "erase.end"))
    for t in times:
        assert any(abs(t - (end + 700)) < 1e-6 for end in ends)
