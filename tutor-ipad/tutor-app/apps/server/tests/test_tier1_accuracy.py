"""Phase 3 acceptance: tier-1 reads the real math checkpoints within the thresholds.

Loads the real reader (Qwen3.5-4B, ~4 GB): runs only with RUN_MODEL_TESTS=1 (and enough free memory).
"""

import asyncio
import os
import tomllib
from pathlib import Path

import pytest
from inktutor_server.config import Settings, configure_hf
from inktutor_server.fixtures import list_fixtures, load, page_strokes_at
from inktutor_server.ink.regions import find_regions
from inktutor_server.recognize.normalize import cer, norm_latex

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_MODEL_TESTS") != "1", reason="set RUN_MODEL_TESTS=1 (loads the reader)"
)
THRESH = tomllib.loads((Path(__file__).parent / "thresholds.toml").read_text())


def test_normalization_makes_spacing_irrelevant():
    a = r"\int _ { 0 } ^ { 1 } x ^ { 3 } d x = \left. \frac { x ^ { 5 } } { 3 } \right| _ { 0 } ^ { 1 }"
    b = r"\int_{0}^{1} x^{3}\,dx=\left[\frac{x^{5}}{3}\right]_{0}^{1}"
    assert norm_latex(a) == norm_latex(b)


@pytest.fixture(scope="module")
def recognizer():
    from inktutor_server.recognize.math import MathRecognizer
    from inktutor_server.recognize.reader import Reader
    from inktutor_server.runtime import memory
    from inktutor_server.runtime.manifest import load_manifest, snapshot_path

    s = Settings()
    configure_hf(s)
    if memory.available_gb() < 6:
        pytest.skip("not enough free memory for the reader")
    rd = Reader(snapshot_path(load_manifest(s.manifest_path)["tier1.text"]))
    asyncio.run(rd.load())
    yield MathRecognizer(rd)
    rd.close()


def math_checkpoints():
    for p in list_fixtures():
        rec = load(p)
        if rec.mode != "math":
            continue
        for cp in rec.checkpoints:
            if cp.truth:
                yield pytest.param(rec, cp, id=f"{p.stem[-8:]}-{cp.id}")


@pytest.mark.parametrize("rec,cp", list(math_checkpoints()))
def test_reading_matches_truth(recognizer, rec, cp):
    strokes = page_strokes_at(rec, cp.t)
    by = {s.id: s for s in strokes}
    regions = find_regions(strokes)
    lines = [asyncio.run(recognizer.read(r, by)) for r in regions]
    truth = [t for t in cp.truth.splitlines() if t.strip()]
    divisions = [t for t in truth if t.startswith("longdiv")]
    chains = [t for t in truth if not t.startswith("longdiv")]
    # a line in the truth may be the continuation ("= □") of the line above: compare joined chains
    read_chains = [ln.latex or "" for ln in lines if ln.kind == "math"]
    if chains:
        joined_truth = [
            norm_latex(" ".join(chains[i : i + 2]))
            if i + 1 < len(chains) and chains[i + 1].lstrip().startswith("=")
            else norm_latex(chains[i])
            for i in range(len(chains))
            if not chains[i].lstrip().startswith("=")
        ]
        got = [norm_latex(x.replace("\n", " ")) for x in read_chains]
        assert len(got) == len(joined_truth), (got, joined_truth)
        for g, t in zip(got, joined_truth, strict=True):
            assert cer(g, t) <= THRESH["math"]["max_cer"], f"CER {cer(g, t):.2f}\n read  {g}\n truth {t}"
    for want, line in zip(divisions, [ln for ln in lines if ln.kind == "long_division"], strict=True):
        dvd, rest = want.removeprefix("longdiv ").split(" / ")
        dvs, q = rest.split(";")[0].split(" = ")
        st = line.structure or {}
        assert (st.get("dividend"), st.get("divisor"), st.get("quotient")) == (dvd.strip(), dvs.strip(), q.strip())
