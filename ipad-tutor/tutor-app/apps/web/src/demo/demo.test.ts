import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { play, type Target } from "./replay";
import { toTimeline, type DemoEvent, type Step } from "./timeline";

// a recording in the recorder's event format: one stroke, a page wipe with the eraser, a second stroke
const events: DemoEvent[] = [
  { type: "page.new", page: "p1", t: 0 },
  { type: "stroke.start", id: "s1", pt: [8000, 10, 20, 0.2, 40, -8], t: 8000 },
  { type: "stroke.points", id: "s1", pts: [[8010, 11, 21, 0.3, 40, -8], [8008, 12, 22, 0.3, 40, -8]], t: 8010 },
  { type: "stroke.end", id: "s1", t: 8030 },
  { type: "tool", eraser: true, t: 9000 },
  { type: "erase.end", t: 9100 }, // a tap that hit nothing
  { type: "erase", ids: ["s1"], x: 11, y: 21, t: 9200 },
  { type: "erase", ids: ["s9"], x: 50, y: 60, t: 9300 },
  { type: "erase.end", t: 9350 },
  { type: "tool", eraser: false, t: 9400 },
  { type: "stroke.start", id: "s2", pt: [10000, 30, 40, 0.2, 0, 0], t: 10000 },
  { type: "stroke.end", id: "s2", t: 10050 },
  { type: "session.end", t: 11000 },
];

const kinds = (steps: Step[]) => steps.map((s) => (s.kind === "page" ? "page" : `${s.eraser ? "E" : "P"}:${s.kind}`));

describe("demo timeline", () => {
  it("turns pen strokes and eraser hits into pointer gestures", () => {
    const steps = toTimeline(events);
    expect(kinds(steps)).toEqual([
      "P:down", "P:move", "P:move", "P:up",
      "E:down", "E:move", "E:up", // the empty tap is skipped; hits are where the eraser went
      "P:down", "P:up",
    ]);
    const down = steps[0]!;
    expect(down).toMatchObject({ t: 8000, x: 10, y: 20, p: 0.2, tx: 40, ty: -8 });
    expect(steps[4]).toMatchObject({ t: 9200, x: 11, y: 21 });
    expect(steps[6]).toMatchObject({ t: 9350, x: 50, y: 60 }); // lifted where the last hit was
  });

  it("keeps time monotonic and starts a page for every page.new after the first", () => {
    const steps = toTimeline([...events, { type: "page.new", page: "p2", t: 12000 }]);
    const ts = steps.map((s) => s.t);
    expect(ts).toEqual([...ts].sort((a, b) => a - b));
    expect(steps[2]!.t).toBe(8010); // a coalesced point stamped 8008 doesn't go back in time
    expect(steps.filter((s) => s.kind === "page")).toHaveLength(1);
  });

  it("aims a hit the recorder rounded out of eraser reach at the stroke it erased", () => {
    const steps = toTimeline([
      { type: "stroke.start", id: "s1", pt: [0, 100, 100, 0.2, 0, 0], t: 0 },
      { type: "stroke.end", id: "s1", t: 10 },
      { type: "erase", ids: ["s1"], x: 112, y: 100, t: 500 }, // 12 px away: hitStrokes (r < 12) would miss
      { type: "erase.end", t: 520 },
    ]);
    expect(kinds(steps)).toEqual(["P:down", "P:up", "E:down", "E:move", "E:up"]);
    expect(steps[3]).toMatchObject({ x: 100, y: 100 });
  });

  it("closes a gesture left open at the end", () => {
    const steps = toTimeline([{ type: "stroke.start", id: "s", pt: [5, 1, 2, 0.5, 0, 0], t: 5 }]);
    expect(kinds(steps)).toEqual(["P:down", "P:up"]);
  });
});

describe("demo player", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  const recorder = () => {
    const got: string[] = [];
    const target: Target = { pointer: (s) => got.push(`${s.kind}@${Date.now()}`), newPage: () => got.push("page") };
    return { got, target };
  };

  it("plays with the recorded timing after a short lead-in", async () => {
    const { got, target } = recorder();
    const t0 = Date.now();
    const p = play(toTimeline(events), target, { now: () => Date.now(), leadInMs: 1000 });
    await vi.advanceTimersByTimeAsync(999);
    expect(got).toHaveLength(0); // the 8 s of silence before the first stroke is cut to 1 s
    await vi.advanceTimersByTimeAsync(1);
    expect(got).toEqual([`down@${t0 + 1000}`]);
    await vi.advanceTimersByTimeAsync(1200); // through the eraser wipe at 9.2–9.35 s
    expect(got.filter((g) => g.startsWith("down"))).toHaveLength(2);
    await vi.advanceTimersByTimeAsync(5000);
    await p.done;
    expect(got).toHaveLength(9);
    expect(got.at(-1)).toBe(`up@${t0 + 3050}`);
  });

  it("runs faster with speed and can be stopped", async () => {
    const { got, target } = recorder();
    const p = play(toTimeline(events), target, { now: () => Date.now(), speed: 2, leadInMs: 0 });
    await vi.advanceTimersByTimeAsync(700); // 1.4 s of recording: the first stroke and the wipe
    expect(got).toHaveLength(7);
    p.stop();
    await p.done;
    await vi.advanceTimersByTimeAsync(5000);
    expect(got).toHaveLength(7);
  });
});
