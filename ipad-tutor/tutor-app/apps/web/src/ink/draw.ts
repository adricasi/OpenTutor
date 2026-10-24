// Stroke rendering and geometry. Rendering matches the recorder (and the server rasterizer):
// width × (0.45 + 1.1·pressure) for pen input, so a light pressure (median 0.16) stays visible.
import type { Stroke } from "../protocol";

export type Pt = Stroke["pts"][number];
export interface Box { x: number; y: number; w: number; h: number }

export function segWidth(s: Pick<Stroke, "width" | "pointer_type">, pressure: number): number {
  const w = s.width ?? 3;
  return s.pointer_type === "pen" && pressure > 0 ? w * (0.45 + 1.1 * pressure) : w;
}

export function drawSegment(ctx: CanvasRenderingContext2D, s: Stroke, a: Pt, b: Pt): void {
  ctx.strokeStyle = s.color ?? "#1F2328";
  ctx.lineWidth = segWidth(s, (a[3] + b[3]) / 2);
  ctx.beginPath();
  ctx.moveTo(a[1], a[2]);
  ctx.lineTo(b[1], b[2]);
  ctx.stroke();
}

export function drawDot(ctx: CanvasRenderingContext2D, s: Stroke, p: Pt): void {
  ctx.fillStyle = s.color ?? "#1F2328";
  ctx.beginPath();
  ctx.arc(p[1], p[2], segWidth(s, p[3]) / 2, 0, Math.PI * 2);
  ctx.fill();
}

export function drawStroke(ctx: CanvasRenderingContext2D, s: Stroke): void {
  const pts = s.pts;
  const first = pts[0];
  if (!first) return;
  if (pts.length === 1) {
    drawDot(ctx, s, first);
    return;
  }
  for (let i = 1; i < pts.length; i++) drawSegment(ctx, s, pts[i - 1]!, pts[i]!);
}

export function strokeBox(s: Stroke): Box {
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (const p of s.pts) {
    if (p[1] < x0) x0 = p[1];
    if (p[1] > x1) x1 = p[1];
    if (p[2] < y0) y0 = p[2];
    if (p[2] > y1) y1 = p[2];
  }
  return { x: x0, y: y0, w: Math.max(0, x1 - x0), h: Math.max(0, y1 - y0) };
}

export function unionBox(boxes: Box[]): Box | null {
  if (!boxes.length) return null;
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (const b of boxes) {
    x0 = Math.min(x0, b.x); y0 = Math.min(y0, b.y);
    x1 = Math.max(x1, b.x + b.w); y1 = Math.max(y1, b.y + b.h);
  }
  return { x: x0, y: y0, w: x1 - x0, h: y1 - y0 };
}

/** Strokes whose points pass within `r` px of (x, y): the eraser removes whole strokes, like the recorder. */
export function hitStrokes(strokes: Stroke[], x: number, y: number, r = 12): Stroke[] {
  return strokes.filter((s) => s.pts.some((p) => Math.abs(p[1] - x) < r && Math.abs(p[2] - y) < r));
}
