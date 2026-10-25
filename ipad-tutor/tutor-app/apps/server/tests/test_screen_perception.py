"""Phases 12–13, the pieces without a socket: AX search and predicates on the synthetic Spanish Excel fixture, frames
(dHash change gate, crops, image pixels → global points), spoken commands, plan validation, and "it never acts"."""

from __future__ import annotations

import base64
import io
import json
import re
import time
from pathlib import Path

import pytest
from inktutor_protocol import AXSnapshot, BBox, DoneWhen, ScreenFrame, Target
from inktutor_server.ax import AXIndex, evaluate
from inktutor_server.config import APP_ROOT
from inktutor_server.guide import load_config, parse_bbox, parse_step, recipe_for
from inktutor_server.screen.commands import parse_screen_command, rung_for
from inktutor_server.screen.frames import FrameState, crop, hamming

FIXTURE = Path(__file__).parent / "fixtures_screen" / "excel_pivot_es.json"
FX = json.loads(FIXTURE.read_text())
SNAP = {k: AXSnapshot.model_validate(v) for k, v in FX["snapshots"].items()}
INSERTAR = Target(title_regex="^Insertar$|^Insert$", role="AXRadioButton", ax_path=["AXTabGroup"])


# ---- AX search ------------------------------------------------------------------------------------------------
def test_insertar_found_by_role_and_title_in_under_100_ms():
    snap = SNAP["start"]
    assert len(snap.elements) >= 280  # a realistic Excel window: ribbon, formula bar, 240 cells, sheet tabs
    t = time.perf_counter()
    index = AXIndex(snap)
    m = index.find(INSERTAR)
    ms = (time.perf_counter() - t) * 1000
    assert m is not None and m.element.role == "AXRadioButton" and m.element.title == "Insertar"
    assert m.element.frame == BBox(x=57, y=71, w=74, h=26)  # not the Inicio ribbon's "Insertar" cells button
    assert ms < 100, f"{ms:.1f} ms"
    t = time.perf_counter()
    for _ in range(100):
        index.find(INSERTAR)
    assert (time.perf_counter() - t) * 10 < 5  # < 5 ms per search on ~300 elements


def test_search_ignores_case_accents_ellipsis_and_role_family():
    index = AXIndex(SNAP["insertar"])
    for target in (
        Target(title_regex="^tabla dinamica$", role="AXButton"),  # no accent
        Target(title_regex="TABLA DINÁMICA", role="AXButton"),
        Target(title_regex="^Tabla dinámica$", role="AXMenuButton"),  # same family as AXButton
        Target(title_regex="^Tabla dinámica$", role="AXRadioButton"),
    ):
        m = index.find(target)
        assert m is not None and m.element.title == "Tabla dinámica", target
    assert index.find(Target(title_regex="dinámica", role="AXTextField")) is None  # another kind, only partly named
    exact = index.find(Target(title_regex="^Tabla dinámica$", role="AXTextField"))  # exact name, wrong role: kept
    assert exact is not None and exact.score < index.find(Target(title_regex="^Tabla dinámica$")).score
    assert index.find(Target(title_regex="^Insertar$", role="AXRadioButton")).element.value == "1"  # selected tab
    assert index.find(Target(title_regex="[unclosed", role="AXButton")) is None  # a bad regex is literal, no crash
    assert index.find(Target(description="only words")) is None  # nothing to search by: the VLM's job


def test_path_breaks_ties_and_secure_fields_are_never_targets():
    index = AXIndex(SNAP["dialog"])
    ok = index.find(Target(title_regex="^Aceptar$", role="AXButton", ax_path=["AXWindow:Crear tabla dinámica"]))
    assert ok is not None and ok.element.id == "dlg-ok"
    snap = AXSnapshot(
        app="X",
        t_ms=0,
        elements=[
            {"id": "pw", "role": "AXSecureTextField", "title": "Contraseña", "frame": {"x": 1, "y": 1, "w": 9, "h": 9}}
        ],
    )
    assert AXIndex(snap).find(Target(title_regex="Contraseña")) is None


def test_done_when_predicates_on_the_fixture():
    tabla = DoneWhen(kind="ax_exists", args={"role": "AXButton", "title_regex": "^Tabla dinámica$"})
    dialog = DoneWhen(kind="ax_exists", args={"role": "AXWindow", "title_regex": "Crear tabla dinámica"})
    pane = DoneWhen(kind="ax_exists", args={"title_regex": "Campos de tabla dinámica"})
    region = DoneWhen(kind="ax_value", args={"title_regex": "^Región$", "value_regex": "^1$"})
    title = DoneWhen(kind="window_title", args={"regex": "crear tabla"})
    idx = {k: AXIndex(v) for k, v in SNAP.items()}
    assert [evaluate(tabla, idx[k]) for k in ("start", "insertar")] == [False, True]
    assert [evaluate(dialog, idx[k]) for k in ("insertar", "dialog")] == [False, True]
    assert [evaluate(pane, idx[k]) for k in ("dialog", "pane")] == [False, True]
    assert [evaluate(region, idx[k]) for k in ("pane", "region")] == [False, True]
    assert [evaluate(title, idx[k]) for k in ("insertar", "dialog")] == [False, True]
    assert evaluate(DoneWhen(kind="user_next"), idx["start"]) is None
    assert evaluate(DoneWhen(kind="vlm_yes_no", args={"question": "?"}), idx["start"]) is None
    # the main window is not a dialog even when focus (window_title) moves to the dialog
    assert list(idx["dialog"].dialogs().values()) == ["Crear tabla dinámica"]
    assert idx["insertar"].dialogs() == {}


def test_ax_summary_is_compact_and_puts_controls_first():
    s = AXIndex(SNAP["insertar"]).summary(120)
    lines = s.splitlines()
    assert len(lines) == 121 and lines[-1].startswith("(+")
    assert any(ln.startswith("AXRadioButton | Insertar") for ln in lines)
    assert any(ln.startswith("AXButton | Tabla dinámica") for ln in lines)


def test_recipe_matches_excel_by_name_or_bundle_and_task_sections_by_goal():
    pivot = "haz una tabla dinámica de ventas"
    assert "Tabla dinámica" in recipe_for("Microsoft Excel", goal=pivot)
    assert "Crear tabla dinámica" in recipe_for("", "com.microsoft.Excel", goal=pivot)
    assert "PivotTable" in recipe_for("Microsoft Excel", goal="make a pivot table")  # English titles too
    other = recipe_for("Microsoft Excel", goal="suma la columna de ventas")
    assert "Insertar / Insert" in other and "Crear tabla dinámica" not in other  # layout yes, pivot steps no
    assert recipe_for("Finder") == ""


# ---- frames ---------------------------------------------------------------------------------------------------
def jpeg_b64(color=(240, 240, 240), boxes=(), size=(400, 300)):
    from PIL import Image, ImageDraw

    img = Image.new("RGB", size, color)
    d = ImageDraw.Draw(img)
    for x, y, w, h, c in boxes:
        d.rectangle([x, y, x + w, y + h], fill=c)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return base64.b64encode(buf.getvalue()).decode()


def frame(b64, **kw):
    base = {"id": "f", "display": 1, "origin": {"x": 0, "y": 0}, "width": 400, "height": 300, "scale": 1}
    return ScreenFrame(**{**base, **kw, "jpeg_b64": b64, "t_ms": 0, "trigger": "tick"})


def test_dhash_change_gate():
    a = FrameState(frame(jpeg_b64(boxes=[(20, 20, 100, 40, (30, 30, 30))])))
    a_again = FrameState(frame(jpeg_b64(boxes=[(20, 20, 100, 40, (30, 30, 30))])))
    b = FrameState(frame(jpeg_b64(boxes=[(200, 150, 150, 120, (200, 30, 30)), (20, 200, 60, 60, (0, 0, 0))])))
    th = load_config().dhash_threshold
    assert a._image is None and a._hash is None  # decoded lazily
    assert hamming(a.hash(), a_again.hash()) <= th  # the same screen re-encoded: no change → no VLM call
    assert hamming(a.hash(), b.hash()) > th  # a dialog opened: changed


def test_image_pixels_map_to_global_points_on_a_second_display_at_scale_2():
    # display 2 sits right of the main one and 200 pt higher; its frame is 1000×600 JPEG px at 2 px per point
    fs = FrameState(
        frame(jpeg_b64(size=(1000, 600)), origin={"x": 1512, "y": -200}, width=1000, height=600, scale=2, display=2)
    )
    c = crop(fs, None, max_px=1024)
    assert c.size == (1000, 600) and c.factor == 1
    assert c.to_global(200, 100, 40, 20) == BBox(x=1612, y=-150, w=20, h=10)
    # cropped to the frontmost window (global points) and downscaled to ≤ 500 px
    win = BBox(x=1612, y=-150, w=300, h=200)  # → frame px (200, 100)–(800, 500)
    c = crop(fs, win, max_px=300)
    assert c.size == (300, 200) and c.factor == pytest.approx(0.5)
    assert c.to_global(0, 0, 30, 20) == BBox(x=1612, y=-150, w=30, h=20)
    assert c.to_global(150, 100, 2, 2).x == pytest.approx(1612 + 150)


def test_parse_bbox_accepts_pixels_box2d_and_corners():
    assert parse_bbox({"type": "bbox", "x": 10, "y": 20, "w": 30, "h": 40}, (100, 100)) == (10, 20, 30, 40)
    assert parse_bbox({"box_2d": [100, 200, 300, 400]}, (1000, 500)) == (200, 50, 200, 100)
    assert parse_bbox({"bbox": [10, 10, 50, 30]}, (100, 100)) == (10, 10, 40, 20)
    assert parse_bbox({"x": 95, "y": 95, "w": 50, "h": 50}, (100, 100)) == (95, 95, 5, 5)  # clamped
    assert parse_bbox({"x": "a"}, (100, 100)) is None
    assert parse_bbox({"x": 500, "y": 500, "w": 5, "h": 5}, (100, 100)) is None


def test_vlm_box_snaps_to_the_ax_frame_under_it():
    index = AXIndex(SNAP["insertar"])
    near = BBox(x=60, y=73, w=70, h=22)  # the VLM's box around "Insertar", a few points off
    assert index.overlapping(near).title == "Insertar"
    assert index.overlapping(BBox(x=1400, y=950, w=5, h=5)) is None


# ---- voice commands -------------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("text", "cmd"),
    [
        ("Next.", "next"),
        ("next step please", "next"),
        ("Siguiente", "next"),
        ("Següent.", "next"),
        ("ya está", "next"),
        ("Back", "back"),
        ("atrás", "back"),
        ("Enrere", "back"),
        ("Repeat that", "repeat"),
        ("Repite, por favor", "repeat"),
        ("repeteix", "repeat"),
        ("Stop", "stop"),
        ("para la guía", "stop"),
        ("Detente.", "stop"),
        ("Atura", "stop"),
        ("Para", None),  # Spanish "para" alone means "for", never "stop"
        ("para qué", "why"),
        ("Why?", "why"),
        ("¿Por qué?", "why"),
        ("Per què?", "why"),
        ("What does this do?", "what"),
        ("¿Qué hace esto?", "what"),
        ("Show me all the steps", "all_steps"),
        ("muéstrame todos los pasos", "all_steps"),
        ("a hint", "hint"),
        ("más", "more"),
        ("what's the next thing in the insert menu", None),  # long: a question, not "next"
        ("haz una tabla dinámica de ventas por región", None),  # a goal
        ("¿dónde está la cinta?", None),
    ],
)
def test_screen_voice_commands(text, cmd):
    assert parse_screen_command(text) == cmd


def test_spoken_ladder_rungs():
    assert [rung_for("why", 1), rung_for("what", 1), rung_for("all_steps", 2), rung_for("solution", 1)] == [2, 3, 4, 4]
    assert [rung_for("hint", 1), rung_for("hint", 2), rung_for("more", 2), rung_for("more", 3)] == [2, 3, 3, 3]


# ---- plan validation ------------------------------------------------------------------------------------------
def test_steps_are_validated_capped_and_never_type_a_password():
    long = " ".join(f"w{i}" for i in range(30))
    st = parse_step(
        {"type": "step", "instruction": "Click Insert", "speak": long, "done_when": {"kind": "teleport"}}, 1
    )
    assert st is not None and len(st.speak.split()) == 15 and st.done_when.kind == "user_next"
    st = parse_step(FX["plan"][0], 1, "p9")
    assert st.id == "p9s1" and st.target.role == "AXRadioButton" and st.done_when.kind == "ax_exists"
    assert parse_step({"type": "step", "instruction": ""}, 1) is None
    assert parse_step({"type": "mark", "instruction": "x"}, 1) is None
    secret = {"type": "step", "instruction": "Escribe tu contraseña", "target": {"role": "AXSecureTextField"}}
    assert parse_step(secret, 1) is None
    assert parse_step({"type": "step", "instruction": "Type your password"}, 1) is None
    args = {"type": "step", "instruction": "x", "done_when": {"kind": "ax_value", "args": {"value_regex": 1}}}
    assert parse_step(args, 1).done_when.args == {"value_regex": "1"}  # args become strings


# ---- it points, it never acts ---------------------------------------------------------------------------------
ACTING = re.compile(
    r"CGEventPost|CGEventCreate(?:Mouse|Keyboard|Scroll)|AXUIElementPerformAction|kAXPressAction|\"AXPress\"|"
    r"AXUIElementSetAttributeValue|pyautogui|pynput|Quartz\.CoreGraphics|osascript|cliclick|System Events"
)


def test_no_server_code_can_click_type_or_scroll():
    src = APP_ROOT / "apps" / "server" / "src"
    hits = [
        f"{p.relative_to(src)}:{i}"
        for p in src.rglob("*.py")
        for i, line in enumerate(p.read_text().splitlines(), 1)
        if ACTING.search(line)
    ]
    assert hits == []
    from inktutor_protocol.models import OverlayEventType

    assert set(OverlayEventType.__args__) == {"highlight", "arrow", "caption", "pulse", "clear", "phantom_state"}
