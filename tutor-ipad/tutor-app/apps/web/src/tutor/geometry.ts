// Where tutor output goes on the page. Every mark is anchored (non-negotiable 2): a line, strokes,
// a graph node or a bbox. Nothing anchored to something unknown is drawn.
import type { Anchor, FeedbackEvent, Reading, Stroke } from "../protocol";
import { type Box, strokeBox, unionBox } from "../ink/draw";

export interface PageIndex {
  strokes: Map<string, Stroke>;
  reading: Reading | null;
}

export function anchorBox(a: Anchor, idx: PageIndex): Box | null {
  if (a.stroke_ids?.length) {
    const boxes = a.stroke_ids.flatMap((id) => {
      const s = idx.strokes.get(id);
      return s ? [strokeBox(s)] : [];
    });
    if (boxes.length) return unionBox(boxes);
  }
  if (a.line_id && idx.reading) {
    const line = idx.reading.lines?.find((l) => l.id === a.line_id);
    if (line) return line.bbox;
  }
  if (a.node_id && idx.reading?.graph) {
    const n = idx.reading.graph.nodes?.find((x) => x.id === a.node_id);
    if (n) return n.bbox;
  }
  return a.bbox ?? null;
}

/** Margin glyphs (✓ ✗ ?) for line anchors sit just right of the line; stroke-anchored ones sit at the strokes. */
export function glyphPosition(f: FeedbackEvent, box: Box, pageWidth: number): { x: number; y: number } {
  const lineAnchored = !f.anchor.stroke_ids?.length;
  if (lineAnchored) {
    const x = Math.min(box.x + box.w + 28, pageWidth - 18); // in the margin, clear of the last symbol
    return { x, y: box.y + box.h / 2 };
  }
  return { x: box.x + box.w + 10, y: box.y - 6 };
}

/** Ghost ink goes beneath the anchor, in the empty space, never between the user's strokes. */
export function ghostPosition(box: Box, pageHeight: number): { x: number; y: number } {
  const below = box.y + box.h + 30;
  return below < pageHeight - 10 ? { x: box.x, y: below } : { x: box.x + box.w + 24, y: box.y + box.h / 2 };
}

export function visibleFeedback(all: FeedbackEvent[]): FeedbackEvent[] {
  const retracted = new Set(all.filter((f) => f.type === "retract" && f.refers_to).map((f) => f.refers_to!));
  const superseded = new Set(all.filter((f) => f.type !== "retract" && f.refers_to).map((f) => f.refers_to!));
  return all.filter((f) => f.type !== "retract" && !retracted.has(f.id) && !superseded.has(f.id));
}

/** Diagram ghosts (missing branches/boxes) stack under the anchor node's subtree, at its children's indent. */
export function diagramGhostPositions(ghosts: FeedbackEvent[], idx: PageIndex, pageHeight: number): Map<string, { x: number; y: number }> {
  const out = new Map<string, { x: number; y: number }>();
  const g = idx.reading?.graph;
  if (!g) return out;
  const kids = new Map<string, string[]>();
  for (const e of g.edges ?? []) kids.set(e.src, [...(kids.get(e.src) ?? []), e.dst]);
  const box = (id: string) => (g.nodes ?? []).find((n) => n.id === id)?.bbox ?? null;
  const subtree = (id: string): string[] => [id, ...(kids.get(id) ?? []).flatMap(subtree)];
  const stacked = new Map<string, number>();
  for (const f of ghosts) {
    const anchor = f.anchor.node_id ?? f.anchor.line_id;
    if (!anchor) continue;
    const boxes = subtree(anchor).map(box).filter((b): b is NonNullable<typeof b> => !!b);
    const own = box(anchor);
    if (!own || !boxes.length) continue;
    const children = (kids.get(anchor) ?? []).map(box).filter((b): b is NonNullable<typeof b> => !!b);
    const x = children.length ? Math.min(...children.map((b) => b.x)) : own.x + 24;
    const bottom = Math.max(...boxes.map((b) => b.y + b.h));
    const n = stacked.get(anchor) ?? 0;
    stacked.set(anchor, n + 1);
    out.set(f.id, { x, y: Math.min(bottom + 34 + n * 36, pageHeight - 12) });
  }
  return out;
}
