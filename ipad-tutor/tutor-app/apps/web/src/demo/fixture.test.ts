// make demo, end to end in jsdom: the demo fixture played into a real InkSurface (pen + eraser wipe) must
// leave the server with exactly the recording's final page, as the ink.update messages describe it.
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { DirtyTracker } from "../ink/dirty";
import { InkSurface } from "../ink/surface";
import type { InkUpdate, Stroke } from "../protocol";
import { canvasTarget, play } from "./replay";
import { toTimeline, type DemoEvent } from "./timeline";

interface Rec { events: DemoEvent[]; pages: { strokes: { pts: number[][] }[] }[] }
const recs = import.meta.glob<Rec>("../../../../../ink-tutor/fixtures/recordings/rec-math-math-22r_69.json", { eager: true, import: "default" });
const rec = Object.values(recs)[0]!;

beforeEach(() => {
  vi.useFakeTimers();
  // jsdom has no 2D canvas: drawing is a no-op here, the stroke data is what's under test
  const ctx = new Proxy({}, { get: () => () => undefined, set: () => true });
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(ctx as unknown as CanvasRenderingContext2D);
});
afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); });

it("replaying rec-math-math-22r_69 (with its eraser wipe) leaves the recording's final page", async () => {
  const canvas = document.createElement("canvas");
  canvas.className = "ink";
  document.body.append(canvas);
  const sent: InkUpdate[] = [];
  const tracker = new DirtyTracker((m) => sent.push(m), 700);
  const surface = new InkSurface({ canvas, tracker, t0: performance.now() });

  const steps = toTimeline(rec.events);
  const player = play(steps, canvasTarget(canvas, (on) => { surface.eraser = on; }, () => surface.newPage()),
    { now: () => Date.now() });
  await vi.advanceTimersByTimeAsync(steps.at(-1)!.t + 5000);
  await player.done;

  const server = new Map<string, Stroke>(); // what the server holds after applying every update
  for (const m of sent) {
    for (const s of m.strokes_changed ?? []) server.set(s.id, s);
    for (const id of m.strokes_removed ?? []) server.delete(id);
  }
  const want = rec.pages[0]!.strokes;
  expect(server.size).toBe(want.length);
  const have = [...server.values()];
  for (let i = 0; i < want.length; i++) {
    const w = want[i]!.pts, h = have[i]!.pts;
    expect(h.length, `stroke ${i} point count`).toBe(w.length);
    expect(h.every((p, j) => Math.abs(p[1] - w[j]![1]!) < 0.11 && Math.abs(p[2] - w[j]![2]!) < 0.11)).toBe(true);
  }
  expect(sent.length).toBeGreaterThan(5); // batched by pen-up debounce, like a hand
  surface.dispose();
  canvas.remove();
});
