"""Region grouping on the real recorded pages (geometry only, no models)."""

from inktutor_server.fixtures import list_fixtures, load, page_strokes_at
from inktutor_server.ink.raster import rasterize
from inktutor_server.ink.regions import division_rows, find_regions


def rec(stem):
    return load(next(p for p in list_fixtures() if p.stem == stem))


MATH = "rec-math-math-22r_69"


def test_each_integral_is_one_region_as_it_grows():
    r = rec(MATH)
    first_ids = None
    for t in (30_000, 52_000):
        regs = find_regions(page_strokes_at(r, t))
        assert len(regs) == 1
        first_ids = first_ids or regs[0].id
        assert regs[0].id == first_ids  # id stays stable while the line grows (marks stay anchored)
    regs = find_regions(page_strokes_at(r, 89_500))
    assert [len(x.stroke_ids) for x in regs] == [60, 36]
    assert regs[0].id == first_ids


def test_long_divisions_are_found_with_their_parts():
    strokes = page_strokes_at(rec(MATH), 154_000)
    regs = find_regions(strokes)
    assert [x.kind for x in regs] == ["long_division", "long_division"]
    assert sum(len(x.stroke_ids) for x in regs) == len(strokes) == 37
    left, right = regs
    assert left.bbox.x < right.bbox.x
    assert {k: len(v) for k, v in left.parts.items()} == {
        "bracket": 1,
        "dividend": 3,
        "divisor": 3,
        "quotient": 2,
        "rows": 10,
    }
    assert len(right.parts["divisor"]) == 2 and len(right.parts["quotient"]) == 2  # "4" (2 strokes) and "5"
    by_id = {s.id: s for s in strokes}
    assert len(division_rows(left, by_id)) >= 4  # 25 / line / 00 / 00 / line / 0


def test_division_detection_can_be_turned_off_for_diagrams():
    strokes = page_strokes_at(rec("rec-math-consulting-case-z1b_a2"), 1e12)
    assert all(x.kind == "line" for x in find_regions(strokes, divisions=False))


def test_rasterizer_is_deterministic_and_crops():
    strokes = page_strokes_at(rec(MATH), 52_000)
    a, b = rasterize(strokes), rasterize(strokes)
    assert a.tobytes() == b.tobytes()
    assert a.size[0] < 1194 and a.getextrema()[0][0] < 50  # cropped, has dark ink
