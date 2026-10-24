// `make demo`: turn a recording's event log into pen gestures to play into the canvas.
// Pen strokes become down/move/up at each recorded point; eraser hits become an eraser gesture through
// the recorded hit positions (the surface's own hit test erases the same strokes, since they sit at the
// same coordinates). The recorder rounds hit positions to whole pixels, which can put a hit just outside
// the 12 px eraser reach, so a hit that would miss its stroke also passes over that stroke's nearest point.
// The tool is implied by the gesture, so a missing `tool` event can't draw an erase.

export interface DemoEvent { type: string; t: number; [k: string]: unknown }

export interface DemoScript {
  stem: string;
  mode: string;
  problem: string;
  duration_ms: number;
  page: { width: number; height: number } | null;
  events: DemoEvent[];
}

export type Step =
  | { t: number; kind: "down" | "move" | "up"; eraser: boolean; x: number; y: number; p: number; tx: number; ty: number }
  | { t: number; kind: "page" };

export type PenStep = Extract<Step, { kind: "down" | "move" | "up" }>;
type RecPt = [number, number, number, number?, number?, number?];
const REACH = 11; // < hitStrokes' 12 px, with margin for the surface's 0.1 px rounding

export function toTimeline(events: DemoEvent[]): Step[] {
  const steps: Step[] = [];
  let last = 0; // keep time monotonic (coalesced points can carry slightly older stamps)
  let pages = 0;
  const g: { pen: PenStep | null } = { pen: null }; // last point of the gesture in progress
  const drawn = new Map<string, RecPt[]>(); // recorded points per stroke id (to aim eraser hits)
  const at = (t: number) => (last = Math.max(last, t));
  const point = (kind: "down" | "move" | "up", eraser: boolean, t: number, x: number, y: number, p = 0.5, tx = 0, ty = 0) => {
    const s = { t: at(t), kind, eraser, x, y, p, tx, ty };
    steps.push(s);
    g.pen = kind === "up" ? null : s;
  };
  const fromPt = (kind: "down" | "move", pt: RecPt, t: number) =>
    point(kind, false, Number.isFinite(pt[0]) ? pt[0] : t, pt[1], pt[2], pt[3] ?? 0.5, pt[4] ?? 0, pt[5] ?? 0);
  const lift = (t: number) => {
    const pen = g.pen;
    if (pen) point("up", pen.eraser, t, pen.x, pen.y, pen.p, pen.tx, pen.ty);
  };

  for (const e of events) {
    switch (e.type) {
      case "page.new":
        if (pages++ > 0) { lift(e.t); steps.push({ t: at(e.t), kind: "page" }); }
        break;
      case "stroke.start":
        lift(e.t);
        drawn.set(String(e.id), [e.pt as RecPt]);
        fromPt("down", e.pt as RecPt, e.t);
        break;
      case "stroke.points":
        drawn.get(String(e.id))?.push(...(e.pts as RecPt[]));
        if (g.pen && !g.pen.eraser) for (const pt of e.pts as RecPt[]) fromPt("move", pt, e.t);
        break;
      case "stroke.end":
        if (g.pen && !g.pen.eraser) lift(e.t);
        break;
      case "erase": {
        const x = Number(e.x), y = Number(e.y);
        if (!Number.isFinite(x) || !Number.isFinite(y)) break;
        if (g.pen && !g.pen.eraser) lift(e.t);
        point(g.pen ? "move" : "down", true, e.t, x, y);
        for (const id of (e.ids as string[] | undefined) ?? []) {
          const pts = drawn.get(String(id)) ?? [];
          const dist = (p: RecPt) => Math.max(Math.abs(p[1] - x), Math.abs(p[2] - y));
          const near = pts.reduce<RecPt | null>((b, p) => (!b || dist(p) < dist(b) ? p : b), null);
          if (near && dist(near) >= REACH) point("move", true, e.t, near[1], near[2]);
        }
        break;
      }
      case "erase.end":
        if (g.pen?.eraser) lift(e.t);
        break;
    }
  }
  lift(last);
  return steps;
}

/** Recording time of the last step (the replay's length). */
export const timelineEnd = (steps: Step[]) => steps[steps.length - 1]?.t ?? 0;
