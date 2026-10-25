"""Search one accessibility snapshot: find a step's target by role + title + path, list dialogs, summarise for the
planner. Matching is case- and accent-insensitive ("Insertar" = "INSERTAR", "Tabla dinamica" = "Tabla dinámica"), and
a trailing "…" on a title is ignored. One `AXIndex` is built per snapshot (titles normalised once), so a search over
a few hundred elements takes well under a millisecond (Phase 12's "done when": < 100 ms).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache

from inktutor_protocol import AXElement, AXSnapshot, BBox, Target

# roles that do the same job: a planner that says "AXButton" for a ribbon tab (an AXRadioButton) still finds it
FAMILIES: dict[str, str] = {
    **dict.fromkeys(
        (
            "AXButton",
            "AXRadioButton",
            "AXTab",
            "AXMenuButton",
            "AXPopUpButton",
            "AXCheckBox",
            "AXMenuItem",
            "AXMenuBarItem",
            "AXLink",
            "AXDisclosureTriangle",
            "AXSegment",
        ),
        "press",
    ),
    **dict.fromkeys(("AXTextField", "AXTextArea", "AXComboBox", "AXSearchField", "AXSecureTextField"), "text"),
    **dict.fromkeys(("AXWindow", "AXSheet", "AXDialog", "AXDrawer", "AXPopover"), "window"),
    **dict.fromkeys(("AXCell", "AXRow", "AXColumn", "AXStaticText"), "content"),
    **dict.fromkeys(("AXGroup", "AXScrollArea", "AXSplitGroup", "AXList", "AXOutline", "AXTable", "AXTabGroup"), "box"),
}
DIALOG_ROLES = {"AXSheet", "AXDialog", "AXWindow", "AXPopover"}
SECURE_ROLES = {"AXSecureTextField"}
_ELLIPSIS = re.compile(r"\s*(?:…|\.\.\.)\s*$")


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def norm(s: str | None) -> str:
    """What a title is compared as: no accents, lower case, single spaces, no trailing ellipsis."""
    if not s:
        return ""
    t = strip_accents(s).replace(" ", " ").casefold()
    return _ELLIPSIS.sub("", " ".join(t.split()))


@lru_cache(maxsize=512)
def rx(pattern: str) -> re.Pattern[str]:
    """A planner's regex, accent-insensitive; an invalid one is matched literally."""
    p = strip_accents(pattern)
    try:
        return re.compile(p, re.I)
    except re.error:
        return re.compile(re.escape(p), re.I)


def family(role: str | None) -> str:
    return FAMILIES.get(role or "", role or "")


@dataclass
class Match:
    element: AXElement
    score: float


class AXIndex:
    """One snapshot, ready to search. Elements keep their tree order (the helper sends them depth first)."""

    def __init__(self, snap: AXSnapshot) -> None:
        self.snap = snap
        self.elements = snap.elements
        self.titles = [norm(e.title) for e in snap.elements]
        self.paths = [[norm(p) for p in e.path] for e in snap.elements]
        self.by_id = {e.id: e for e in snap.elements}

    # ---- the planner's target
    def find(self, target: Target) -> Match | None:
        """The element a step points at, or None. Title (regex) and role decide; the path breaks ties."""
        if target.title_regex is None and not target.ax_path:
            return None
        pat = rx(target.title_regex) if target.title_regex else None
        want_path = [norm(p) for p in target.ax_path or []]
        best: Match | None = None
        for i, e in enumerate(self.elements):
            title = self.titles[i]
            if pat is not None:
                if not title or not pat.search(title):
                    continue
            elif not want_path or not _path_hit(want_path[-1], [*self.paths[i], f"{norm(e.role)}:{title}"]):
                continue
            score = 10.0
            if target.role:
                if e.role == target.role:
                    score += 3
                elif family(e.role) == family(target.role):
                    score += 1
                elif pat is not None and pat.fullmatch(title):
                    score -= 4  # another kind of element with exactly that name: only if nothing better exists
                else:
                    continue
            if pat is not None and pat.fullmatch(title):
                score += 1
            score += sum(1.5 for p in want_path if _path_hit(p, self.paths[i]))
            if e.enabled:
                score += 1
            if e.frame.w <= 0 or e.frame.h <= 0:
                score -= 5
            if e.role in SECURE_ROLES:
                continue  # never point at a password field
            if best is None or score > best.score:
                best = Match(e, score)
        return best

    # ---- predicates (done_when)
    def exists(self, title_regex: str | None, role: str | None = None) -> AXElement | None:
        if not title_regex:
            return None
        pat = rx(title_regex)
        for i, e in enumerate(self.elements):
            if self.titles[i] and pat.search(self.titles[i]) and _role_ok(e.role, role):
                return e
        return None

    def value(self, title_regex: str | None, value_regex: str | None, role: str | None = None) -> AXElement | None:
        if not value_regex:
            return None
        tpat, vpat = (rx(title_regex) if title_regex else None), rx(value_regex)
        for i, e in enumerate(self.elements):
            if tpat is not None and not (self.titles[i] and tpat.search(self.titles[i])):
                continue
            if e.value is not None and vpat.search(norm(e.value)) and _role_ok(e.role, role):
                return e
        return None

    def dialogs(self) -> dict[str, str]:
        """Sheets, dialogs, popovers and every window but the main one (the largest): normalised title → title.
        Focus moving to a dialog changes `window_title`, so the main window is told apart by size, not by name."""
        windows = [e for e in self.elements if e.role == "AXWindow"]
        main = max(windows, key=lambda e: e.frame.w * e.frame.h).id if windows else None
        out: dict[str, str] = {}
        for i, e in enumerate(self.elements):
            if e.role in DIALOG_ROLES and self.titles[i] and e.id != main:
                out[self.titles[i]] = (e.title or "").strip()
        return out

    def overlapping(self, box: BBox, min_iou: float = 0.3) -> AXElement | None:
        """The control under a VLM box (best overlap), to snap the box to its exact frame. Containers and windows
        are skipped: only something you can click or read."""
        best, best_iou = None, min_iou
        for e in self.elements:
            if family(e.role) in ("window", "box") or e.frame.w <= 0 or e.frame.h <= 0:
                continue
            v = iou(e.frame, box)
            if v > best_iou:
                best, best_iou = e, v
        return best

    # ---- what the planner sees
    def summary(self, limit: int = 120) -> str:
        """`role | title | value | (disabled) | path tail`, one line per element, the most useful `limit` first
        (named, enabled, clickable), in tree order. Values are cut short; secure fields never have one."""

        def rank(i: int) -> tuple[int, int]:
            e = self.elements[i]
            named = bool(self.titles[i])
            return (0 if named and family(e.role) == "press" else 1 if named else 2, i)

        keep = sorted(sorted(range(len(self.elements)), key=rank)[:limit])
        lines = []
        for i in keep:
            e = self.elements[i]
            parts = [e.role, (e.title or "").strip()[:60]]
            if e.value and e.role not in SECURE_ROLES:
                parts.append(f"value={e.value.strip()[:30]}")
            if not e.enabled:
                parts.append("(disabled)")
            if e.path:
                parts.append(" > ".join(e.path[-2:]))
            lines.append(" | ".join(parts))
        more = len(self.elements) - len(keep)
        return "\n".join(lines) + (f"\n(+{more} more elements)" if more > 0 else "")


def _path_hit(want: str, path: list[str]) -> bool:
    """`AXTabGroup` or `AXRadioButton:Insertar` (normalised) against one element's path segments."""
    return any(seg == want or seg.startswith(want) or (":" in want and want in seg) for seg in path)


def _role_ok(actual: str, wanted: str | None) -> bool:
    return not wanted or actual == wanted or family(actual) == family(wanted)


def iou(a: BBox, b: BBox) -> float:
    ix = max(0.0, min(a.x + a.w, b.x + b.w) - max(a.x, b.x))
    iy = max(0.0, min(a.y + a.h, b.y + b.h) - max(a.y, b.y))
    inter = ix * iy
    union = a.w * a.h + b.w * b.h - inter
    return inter / union if union > 0 else 0.0
