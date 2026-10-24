"""`done_when` on an accessibility snapshot: state predicates first (SCREEN-TUTOR.md, "Completion detection").

`evaluate` answers True/False for the predicates the snapshot can decide (`ax_exists`, `ax_value`, `window_title`)
and None for the ones it can't (`vlm_yes_no` needs a frame and the tutor; `user_next` needs the user).
"""

from __future__ import annotations

from inktutor_protocol import AXElement, DoneWhen

from .search import AXIndex, norm, rx

SNAPSHOT_KINDS = {"ax_exists", "ax_value", "window_title"}


def evaluate(dw: DoneWhen, index: AXIndex | None) -> bool | None:
    if dw.kind not in SNAPSHOT_KINDS:
        return None
    if index is None:
        return False
    return matched(dw, index) is not None or (dw.kind == "window_title" and _title_ok(dw, index))


def matched(dw: DoneWhen, index: AXIndex) -> AXElement | None:
    """The element that makes an `ax_exists`/`ax_value` predicate true, if any."""
    a = dw.args
    if dw.kind == "ax_exists":
        return index.exists(a.get("title_regex") or a.get("regex"), a.get("role"))
    if dw.kind == "ax_value":
        return index.value(a.get("title_regex"), a.get("value_regex") or a.get("regex"), a.get("role"))
    return None


def _title_ok(dw: DoneWhen, index: AXIndex) -> bool:
    pattern = dw.args.get("regex") or dw.args.get("title_regex")
    return bool(pattern) and bool(rx(pattern).search(norm(index.snap.window_title)))


def mentions(dw: DoneWhen, title: str) -> bool:
    """Does this predicate talk about a window/dialog with this (normalised) title?"""
    for key in ("title_regex", "regex"):
        p = dw.args.get(key)
        if p and rx(p).search(title):
            return True
    return False
