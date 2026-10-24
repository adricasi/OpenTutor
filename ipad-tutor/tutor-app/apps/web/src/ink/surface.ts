// The ink layer: the user's strokes, exactly as drawn. Nothing here ever edits a finished stroke
// (non-negotiable 1: ink stays ink). Pointer handling mirrors the recorder, including the iPad fix
// for quick successive Pencil strokes (a new pen-down finishes a stroke whose pointerup is late).
import type { PageRef, Stroke } from "../protocol";
import { DirtyTracker } from "./dirty";
import { drawDot, drawSegment, drawStroke, hitStrokes, type Pt } from "./draw";

type Action = { type: "add" | "erase"; strokes: Stroke[] };

export interface SurfaceOptions {
  canvas: HTMLCanvasElement;
  tracker: DirtyTracker;
  t0: number; // performance.now() at session start
  onChange?: () => void;
}

let counter = 0;
export const uid = (p: string) => `${p}_${Date.now().toString(36)}_${(counter++).toString(36)}`;

export class InkSurface {
  page: PageRef;
  strokes: Stroke[] = [];
  color = "#1F2328";
  width = 3;
  eraser = false;
  fingerDraws = false;
  lastPt: { x: number; y: number } | null = null; // where the pen was last (voice: "why is this wrong?")
  private ctx: CanvasRenderingContext2D;
  private live: Stroke | null = null;
  private active: number | null = null;
  private activeSince = 0;
  private undoStack: Action[] = [];
  private redoStack: Action[] = [];
  private pages = new Map<string, { page: PageRef; strokes: Stroke[] }>();
  private detach: () => void;

  constructor(private o: SurfaceOptions) {
    const ctx = o.canvas.getContext("2d", { desynchronized: true });
    if (!ctx) throw new Error("no 2d context");
    this.ctx = ctx;
    this.page = this.makePage(0);
    this.resize();
    this.detach = this.attach();
  }

  dispose(): void {
    this.detach();
  }

  // ---- pages
  private makePage(index: number): PageRef {
    const r = this.o.canvas.getBoundingClientRect();
    const page = { id: uid("p"), index, width: Math.round(r.width), height: Math.round(r.height) };
    this.pages.set(page.id, { page, strokes: [] });
    return page;
  }

  newPage(): PageRef {
    this.o.tracker.flush(this.page);
    this.page = this.makePage(this.pages.size);
    this.strokes = this.pages.get(this.page.id)!.strokes;
    this.undoStack = [];
    this.redoStack = [];
    this.redraw();
    this.o.onChange?.();
    return this.page;
  }

  get pageList(): PageRef[] {
    return [...this.pages.values()].map((p) => p.page);
  }

  showPage(id: string): void {
    const p = this.pages.get(id);
    if (!p) return;
    this.o.tracker.flush(this.page);
    this.page = p.page;
    this.strokes = p.strokes;
    this.undoStack = [];
    this.redoStack = [];
    this.redraw();
    this.o.onChange?.();
  }

  resize(): void {
    const c = this.o.canvas;
    const r = c.getBoundingClientRect();
    const dpr = Math.max(1, Math.min(3, window.devicePixelRatio || 1));
    c.width = Math.round(r.width * dpr);
    c.height = Math.round(r.height * dpr);
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this.ctx.lineCap = "round";
    this.ctx.lineJoin = "round";
    this.redraw();
  }

  redraw(): void {
    const c = this.o.canvas;
    this.ctx.clearRect(0, 0, c.width, c.height);
    for (const s of this.strokes) drawStroke(this.ctx, s);
    if (this.live) drawStroke(this.ctx, this.live);
  }

  // ---- edits (each one also feeds the dirty tracker)
  undo(): void {
    const a = this.undoStack.pop();
    if (!a) return;
    this.apply(a, true);
    this.redoStack.push(a);
  }

  redo(): void {
    const a = this.redoStack.pop();
    if (!a) return;
    this.apply(a, false);
    this.undoStack.push(a);
  }

  get canUndo(): boolean { return this.undoStack.length > 0; }
  get canRedo(): boolean { return this.redoStack.length > 0; }

  private apply(a: Action, reverse: boolean): void {
    const adding = (a.type === "add") !== reverse;
    for (const s of a.strokes) {
      const i = this.strokes.indexOf(s);
      if (adding && i < 0) { this.strokes.push(s); this.o.tracker.add(s); }
      if (!adding && i >= 0) { this.strokes.splice(i, 1); this.o.tracker.remove(s.id); }
    }
    this.o.tracker.penUp(this.page);
    this.redraw();
    this.o.onChange?.();
  }

  // ---- pointer input
  private now(): number {
    return Math.round((performance.now() - this.o.t0) * 10) / 10;
  }

  private point(e: PointerEvent): Pt {
    const r = this.o.canvas.getBoundingClientRect();
    return [
      this.now(),
      Math.round((e.clientX - r.left) * 10) / 10,
      Math.round((e.clientY - r.top) * 10) / 10,
      Math.round((e.pressure || 0) * 1000) / 1000,
      Math.round(e.tiltX || 0),
      Math.round(e.tiltY || 0),
    ];
  }

  private eraseAt(e: PointerEvent): void {
    const r = this.o.canvas.getBoundingClientRect();
    const hit = hitStrokes(this.strokes, e.clientX - r.left, e.clientY - r.top);
    if (!hit.length) return;
    for (const s of hit) {
      this.strokes.splice(this.strokes.indexOf(s), 1);
      this.o.tracker.remove(s.id);
    }
    this.undoStack.push({ type: "erase", strokes: hit });
    this.redoStack = [];
    this.redraw();
    this.o.onChange?.();
  }

  private endStroke(pointerId: number, timeStamp?: number): void {
    if (pointerId !== this.active) return;
    if (timeStamp !== undefined && timeStamp < this.activeSince) return; // late pointerup of the previous stroke
    this.active = null;
    try { this.o.canvas.releasePointerCapture(pointerId); } catch { /* not captured */ }
    if (this.eraser) { this.o.tracker.penUp(this.page); return; }
    const s = this.live;
    this.live = null;
    if (!s) return;
    this.strokes.push(s);
    this.undoStack.push({ type: "add", strokes: [s] });
    this.redoStack = [];
    this.o.tracker.add(s);
    this.o.tracker.penUp(this.page);
    this.o.onChange?.();
  }

  private attach(): () => void {
    const c = this.o.canvas;
    const accepts = (e: PointerEvent) => e.pointerType !== "touch" || this.fingerDraws;

    const down = (e: PointerEvent) => {
      if (!accepts(e)) return;
      if (this.active !== null) {
        if (e.pointerType === "touch") return; // a second finger: ignore
        this.endStroke(this.active);
      }
      e.preventDefault();
      this.active = e.pointerId;
      this.activeSince = e.timeStamp;
      try { c.setPointerCapture(e.pointerId); } catch { /* synthetic */ }
      this.o.tracker.penDown();
      if (this.eraser) { this.eraseAt(e); return; }
      const p = this.point(e);
      this.lastPt = { x: p[1], y: p[2] };
      const pt = e.pointerType === "pen" || e.pointerType === "touch" ? e.pointerType : "mouse";
      this.live = { id: uid("s"), page_id: this.page.id, color: this.color, width: this.width, pointer_type: pt, pts: [p] };
      drawDot(this.ctx, this.live, p);
    };
    const move = (e: PointerEvent) => {
      if (e.pointerId !== this.active) return;
      e.preventDefault();
      if (this.eraser) { this.eraseAt(e); return; }
      const s = this.live;
      if (!s) return;
      const evs = e.getCoalescedEvents?.().length ? e.getCoalescedEvents() : [e];
      for (const ce of evs) {
        const p = this.point(ce);
        const last = s.pts[s.pts.length - 1]!;
        if (Math.abs(p[1] - last[1]) < 0.3 && Math.abs(p[2] - last[2]) < 0.3) continue;
        s.pts.push(p);
        drawSegment(this.ctx, s, last, p);
      }
    };
    const up = (e: PointerEvent) => this.endStroke(e.pointerId, e.timeStamp);
    const lost = (e: PointerEvent) => { if (this.live && e.pointerId === this.active) this.endStroke(e.pointerId); };
    // keep iOS gesture recognizers (double-tap zoom, Scribble, loupe) off the canvas
    const stop = (e: Event) => { if (e.cancelable) e.preventDefault(); };
    const onResize = () => this.resize();

    c.addEventListener("pointerdown", down);
    c.addEventListener("pointermove", move);
    c.addEventListener("pointerup", up);
    c.addEventListener("pointercancel", up);
    c.addEventListener("lostpointercapture", lost);
    for (const t of ["touchstart", "touchmove", "touchend"]) c.addEventListener(t, stop, { passive: false });
    for (const t of ["gesturestart", "gesturechange", "dblclick"]) c.addEventListener(t, stop);
    window.addEventListener("resize", onResize);
    return () => {
      c.removeEventListener("pointerdown", down);
      c.removeEventListener("pointermove", move);
      c.removeEventListener("pointerup", up);
      c.removeEventListener("pointercancel", up);
      c.removeEventListener("lostpointercapture", lost);
      for (const t of ["touchstart", "touchmove", "touchend"]) c.removeEventListener(t, stop);
      for (const t of ["gesturestart", "gesturechange", "dblclick"]) c.removeEventListener(t, stop);
      window.removeEventListener("resize", onResize);
    };
  }
}
