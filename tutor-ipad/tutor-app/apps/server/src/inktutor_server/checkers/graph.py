"""graph.rules — deterministic structure checks for diagrams (before any LLM).

Issue trees (consulting): every node hangs off the tree; a split has at least two branches.
Boxes and arrows (system design): nothing floats unconnected; a node every path goes through is a
single point of failure worth asking about.
Findings are questions (pins), not grades: a diagram can be unusual on purpose.
"""

from __future__ import annotations

from dataclasses import dataclass

from inktutor_protocol import Graph


@dataclass
class Finding:
    node_id: str
    kind: str  # "orphan" | "single_branch" | "spof"
    text: str  # the question the pin asks


def tree_rules(g: Graph, root: str | None) -> list[Finding]:
    out: list[Finding] = []
    has_parent = {e.dst for e in g.edges}
    children: dict[str, list[str]] = {}
    for e in g.edges:
        children.setdefault(e.src, []).append(e.dst)
    labels = {n.id: n.label for n in g.nodes}
    for n in g.nodes:
        if n.id != root and n.id not in has_parent and n.id not in children:
            out.append(Finding(n.id, "orphan", f"Where does “{n.label}” fit in the tree?"))
    for parent, kids in children.items():
        if len(kids) == 1:
            out.append(
                Finding(
                    parent, "single_branch", f"“{labels.get(parent, '')}” has one branch: what else belongs under it?"
                )
            )
    return out


def system_rules(g: Graph) -> list[Finding]:
    out: list[Finding] = []
    adj: dict[str, set[str]] = {n.id: set() for n in g.nodes}
    for e in g.edges:
        if e.src in adj and e.dst in adj:
            adj[e.src].add(e.dst)
            adj[e.dst].add(e.src)
    labels = {n.id: n.label for n in g.nodes}
    for nid, nb in adj.items():
        if not nb and len(adj) > 1:
            out.append(Finding(nid, "orphan", f"“{labels[nid]}” isn't connected to anything: what talks to it?"))
    for nid in _articulation_points(adj):
        if len(adj[nid]) >= 2:
            out.append(Finding(nid, "spof", f"Everything goes through “{labels[nid]}”: what happens if it fails?"))
    return out


def _articulation_points(adj: dict[str, set[str]]) -> set[str]:
    disc: dict[str, int] = {}
    low: dict[str, int] = {}
    out: set[str] = set()
    t = 0

    def dfs(u: str, parent: str | None) -> None:
        nonlocal t
        disc[u] = low[u] = t
        t += 1
        kids = 0
        for v in adj[u]:
            if v not in disc:
                kids += 1
                dfs(v, u)
                low[u] = min(low[u], low[v])
                if parent is not None and low[v] >= disc[u]:
                    out.add(u)
            elif v != parent:
                low[u] = min(low[u], disc[v])
        if parent is None and kids > 1:
            out.add(u)

    for u in adj:
        if u not in disc:
            dfs(u, None)
    return out
