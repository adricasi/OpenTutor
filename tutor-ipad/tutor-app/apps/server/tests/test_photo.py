"""Phase 8: photos of paper. Chandra's output → bbox-anchored lines → the same checkers; Chandra unloads after."""

import os
import time

import pytest
from inktutor_protocol import ReadingLine
from inktutor_server.agent.marks import verdict_for
from inktutor_server.fixtures import list_fixtures, load
from inktutor_server.recognize.normalize import cer, norm_latex
from inktutor_server.recognize.photo import parse_layout

# Chandra OCR 2 (mixed 4/8) on the recorded photo, verbatim
RAW = "".join(
    [
        '<div data-bbox="38 0 75 27" data-label="Text"><p>Mar</p></div>',
        '<div data-bbox="128 308 887 414" data-label="Equation-Block"><math display="block">',
        "2x + 5 = 4x ; \\quad 5 = 4x - 2x ; \\quad 5 = 3x ; \\quad x = \\frac{5}{3}</math></div>",
        '<div data-bbox="115 437 887 580" data-label="Equation-Block"><math display="block">',
        "\\int_0^2 \\sqrt{x+2} dx = \\left[ \\frac{1}{\\sqrt{x+2}} \\right]_0^2 = \\frac{1}{\\sqrt{2+2}} - ",
        "\\frac{1}{\\sqrt{0+2}} = \\frac{1}{\\sqrt{4}} - \\frac{1}{\\sqrt{2}} =</math></div>",
    ]
)
PHOTO = "rec-math-page-d9x_co"


def photo_fixture():
    """The photo recording is a picture of a handwritten paper page, kept local (not in the repository)."""
    path = next((p for p in list_fixtures() if p.stem == PHOTO), None)
    if path is None:
        pytest.skip(f"{PHOTO}.json is local-only (a photo of a paper page)")
    return load(path)


def photo_truth():
    return photo_fixture().photos[0].truth.splitlines()


def test_layout_becomes_lines_with_boxes_in_photo_pixels():
    lines = parse_layout(RAW, 1600, 1200)
    assert [ln.kind for ln in lines] == ["text", "math", "math"]
    eq = lines[1]
    assert round(eq.bbox.x) == 205 and round(eq.bbox.y) == 370 and round(eq.bbox.w) == 1214
    for got, want in zip([ln.latex for ln in lines[1:]], photo_truth(), strict=True):
        assert cer(norm_latex(got.replace("\\quad", "")), norm_latex(want)) <= 0.15


def test_checkers_find_both_mistakes_on_the_photo():
    lines = parse_layout(RAW, 1600, 1200)
    v1 = verdict_for(lines[1], {})
    v2 = verdict_for(lines[2], {})
    assert v1.ok is False and "3x" in v1.detail and v1.ghost == "5 = 2·x"
    assert v2.ok is False and "antiderivative" in v2.detail
    assert (
        verdict_for(
            ReadingLine(id="t", stroke_ids=[], bbox=lines[0].bbox, kind="text", text="Mar", confidence=0.8), {}
        ).ok
        is None
    )


@pytest.mark.skipif(os.environ.get("RUN_MODEL_TESTS") != "1", reason="set RUN_MODEL_TESTS=1 (loads Chandra)")
def test_photo_through_the_api_and_chandra_is_unloaded(tmp_path):
    from fastapi.testclient import TestClient
    from inktutor_server.app import create_app
    from inktutor_server.config import Settings
    from test_agent import Inbox

    rec = photo_fixture()
    app = create_app(Settings(data_dir=tmp_path, tier1_enabled=False, tier2_provider="none"))
    with TestClient(app) as client:
        from inktutor_server.runtime import Runtime

        rt = Runtime(app.state.svc.settings)
        rt.manifest = __import__("inktutor_server.runtime.manifest", fromlist=["load_manifest"]).load_manifest(
            app.state.svc.settings.manifest_path
        )
        app.state.svc.runtime = rt
        sess = client.post("/api/sessions", json={"mode": "math", "problem": rec.session.problem}).json()
        with client.websocket_connect(f"/ws/session/{sess['id']}") as ws:
            inbox = Inbox(ws)
            inbox.get(5)
            r = client.post(f"/api/sessions/{sess['id']}/photos", json={"data_url": rec.photos[0].data_url}).json()
            assert r["page"]["width"] == 1600 and client.get(r["url"]).status_code == 200
            msgs = inbox.until(lambda m, out: sum(x["type"] == "feedback" for x in out) >= 2, 120)
        fb = [m["feedback"] for m in msgs if m["type"] == "feedback"]
        assert sum(f["glyph"] == "cross" for f in fb) == 2  # both mistakes on the paper
        assert all(f["anchor"]["line_id"].startswith("L_ph_") for f in fb)
        time.sleep(0.5)
        photo = client.get("/api/settings").json()["photo"]
        assert photo == {"loaded": False, "jobs": 1}  # Chandra was unloaded after the job
