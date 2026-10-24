"""Phase 6: diagram geometry, graph rules, and (with the reader) node/edge truth on the recorded issue tree."""

import asyncio
import os

import pytest
from inktutor_protocol import BBox, Graph, GraphEdge, GraphNode
from inktutor_server.checkers.graph import system_rules, tree_rules
from inktutor_server.fixtures import list_fixtures, load, page_strokes_at
from inktutor_server.ink.diagram import layout
from inktutor_server.recognize.normalize import graph_match, parse_graph_truth

CONSULT = "rec-math-consulting-case-z1b_a2"


def rec():
    return load(next(p for p in list_fixtures() if p.stem == CONSULT))


def test_issue_tree_geometry_matches_the_truth_structure():
    lay = layout(page_strokes_at(rec(), 1e12))
    assert len(lay.labels) == 7  # title + 3 branches + 3 sub-branches
    by_pos = sorted(lay.labels, key=lambda r: (r.bbox.y, r.bbox.x))
    title = by_pos[0]
    assert lay.root == title.id
    parents = {}
    for a, b, _ in lay.edges:
        parents.setdefault(a, []).append(b)
    assert len(parents[title.id]) == 3  # Market opportunity, Synergies, Financials
    financials = next(p for p in parents if p != title.id)
    assert len(parents[financials]) == 3  # Price, Revenue, Costs
    assert len(lay.edges) == 6


def node(i, label):
    return GraphNode(id=i, label=label, bbox=BBox(x=0, y=0, w=1, h=1), stroke_ids=[])


def test_tree_rules_flag_single_branches_and_orphans():
    g = Graph(
        nodes=[node("r", "Q"), node("a", "A"), node("b", "B"), node("c", "C")],
        edges=[GraphEdge(id="1", src="r", dst="a"), GraphEdge(id="2", src="a", dst="b")],
    )
    kinds = {(f.node_id, f.kind) for f in tree_rules(g, "r")}
    assert kinds == {("r", "single_branch"), ("a", "single_branch"), ("c", "orphan")}


def test_system_rules_find_a_single_point_of_failure():
    g = Graph(
        nodes=[node("c", "client"), node("api", "API"), node("db", "DB"), node("q", "queue")],
        edges=[GraphEdge(id="1", src="c", dst="api"), GraphEdge(id="2", src="api", dst="db")],
    )
    fs = {(f.node_id, f.kind) for f in system_rules(g)}
    assert ("api", "spof") in fs and ("q", "orphan") in fs


def test_graph_truth_parsing_and_matching():
    truth = rec().checkpoints[0].truth
    nodes, edges = parse_graph_truth(truth)
    assert len(nodes) == 7 and len(edges) == 6
    ids = {f"n{i}": n for i, n in enumerate(nodes)}
    back = {v: k for k, v in ids.items()}
    assert graph_match(ids, [(back[a], back[b]) for a, b in edges], truth) == 1.0


@pytest.mark.skipif(os.environ.get("RUN_MODEL_TESTS") != "1", reason="set RUN_MODEL_TESTS=1 (loads the reader)")
def test_issue_tree_reading_matches_truth_85_percent():
    from inktutor_server.config import Settings, configure_hf
    from inktutor_server.recognize.diagram import DiagramRecognizer
    from inktutor_server.recognize.reader import Reader
    from inktutor_server.runtime.manifest import load_manifest, snapshot_path

    s = Settings()
    configure_hf(s)
    reader = Reader(snapshot_path(load_manifest(s.manifest_path)["tier1.text"]))
    asyncio.run(reader.load())
    r = rec()
    strokes = page_strokes_at(r, 1e12)
    by = {x.id: x for x in strokes}
    lay = layout(strokes)
    rd = DiagramRecognizer(reader)
    labels = {reg.id: asyncio.run(rd.read(reg, by)).text or "" for reg in lay.labels}
    reader.close()
    score = graph_match(labels, [(a, b) for a, b, _ in lay.edges], r.checkpoints[0].truth)
    assert score >= 0.85, (score, labels)
