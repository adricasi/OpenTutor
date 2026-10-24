import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DirtyTracker } from "./ink/dirty";
import { hitStrokes, segWidth, strokeBox } from "./ink/draw";
import { anchorBox, visibleFeedback } from "./tutor/geometry";
import { initialState, reduce } from "./session";
import type { FeedbackEvent, InkUpdate, PageRef, Stroke } from "./protocol";

const page: PageRef = { id: "p1", index: 0, width: 1194, height: 628 };
const stroke = (id: string, x = 10, y = 10): Stroke => ({
  id, page_id: "p1", color: "#1F2328", width: 3, pointer_type: "pen",
  pts: [[0, x, y, 0.2, 0, 0], [5, x + 20, y + 10, 0.3, 0, 0]],
});
const fb = (o: Partial<FeedbackEvent>): FeedbackEvent => ({ id: "f", page_id: "p1", anchor: {}, type: "mark", ...o });

describe("DirtyTracker (pen-up debounce)", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("sends one batch 700 ms after the last pen-up, not while writing", () => {
    const sent: InkUpdate[] = [];
    const t = new DirtyTracker((m) => sent.push(m), 700);
    t.penDown(); t.add(stroke("a")); t.penUp(page);
    vi.advanceTimersByTime(500);
    t.penDown(); t.add(stroke("b")); t.penUp(page); // second stroke within the debounce
    vi.advanceTimersByTime(699);
    expect(sent).toHaveLength(0);
    vi.advanceTimersByTime(1);
    expect(sent).toHaveLength(1);
    expect((sent[0]!.strokes_changed ?? []).map((s) => s.id)).toEqual(["a", "b"]);
    expect(sent[0]!.page).toEqual(page); // first update carries the page
  });

  it("cancels the pending flush on pen-down, and only sends the page once", () => {
    const sent: InkUpdate[] = [];
    const t = new DirtyTracker((m) => sent.push(m), 700);
    t.add(stroke("a")); t.penUp(page); vi.advanceTimersByTime(700);
    t.penDown(); vi.advanceTimersByTime(5000);
    expect(sent).toHaveLength(1);
    t.add(stroke("b")); t.penUp(page); vi.advanceTimersByTime(700);
    expect(sent[1]!.page).toBeNull();
  });

  it("an unsent stroke that is erased is never sent; a sent one is removed", () => {
    const sent: InkUpdate[] = [];
    const t = new DirtyTracker((m) => sent.push(m), 700);
    t.add(stroke("a")); t.penUp(page); vi.advanceTimersByTime(700);
    t.add(stroke("b")); t.remove("b"); t.remove("a"); t.penUp(page); vi.advanceTimersByTime(700);
    expect(sent[1]!.strokes_changed).toEqual([]);
    expect(sent[1]!.strokes_removed).toEqual(["a"]);
  });
});

describe("ink geometry", () => {
  it("renders light pressure with a visible floor, like the recorder", () => {
    expect(segWidth({ width: 3, pointer_type: "pen" }, 0.16)).toBeCloseTo(3 * (0.45 + 1.1 * 0.16));
    expect(segWidth({ width: 3, pointer_type: "mouse" }, 0)).toBe(3);
  });
  it("boxes and hit-tests strokes", () => {
    expect(strokeBox(stroke("a"))).toEqual({ x: 10, y: 10, w: 20, h: 10 });
    expect(hitStrokes([stroke("a"), stroke("b", 300, 300)], 15, 12).map((s) => s.id)).toEqual(["a"]);
  });
});

describe("anchors", () => {
  const idx = {
    strokes: new Map([["a", stroke("a")]]),
    reading: { page_id: "p1", version: 1, lines: [{ id: "L1", stroke_ids: ["a"], bbox: { x: 5, y: 6, w: 100, h: 30 }, kind: "math" as const, confidence: 0.9 }] },
  };
  it("resolves strokes, lines and bboxes; unknown anchors resolve to nothing", () => {
    expect(anchorBox({ stroke_ids: ["a"] }, idx)).toEqual({ x: 10, y: 10, w: 20, h: 10 });
    expect(anchorBox({ line_id: "L1" }, idx)).toEqual({ x: 5, y: 6, w: 100, h: 30 });
    expect(anchorBox({ line_id: "nope" }, idx)).toBeNull();
    expect(anchorBox({ stroke_ids: ["zzz"] }, idx)).toBeNull();
  });
  it("hides retracted and superseded feedback", () => {
    const list = [fb({ id: "m1" }), fb({ id: "m2" }), fb({ id: "r", type: "retract", refers_to: "m1" }), fb({ id: "m3", refers_to: "m2" })];
    expect(visibleFeedback(list).map((f) => f.id)).toEqual(["m3"]);
  });
});

describe("reducer", () => {
  it("replaces feedback with the same id and records the provider badge", () => {
    let s = reduce(initialState, { kind: "server", msg: { type: "feedback", feedback: fb({ id: "c", source: "checker" }), provider: "local", model: "checker", ms: 40 } });
    expect(s.badge).toBeNull(); // checker marks don't claim to be the tutor
    s = reduce(s, { kind: "server", msg: { type: "feedback", feedback: fb({ id: "x", text: "a", source: "tutor" }), provider: "local", model: "gemma4-26b-a4b", ms: 2800 } });
    s = reduce(s, { kind: "server", msg: { type: "feedback", feedback: fb({ id: "x", text: "b" }) } });
    expect(s.feedback["p1"]!.filter((f) => f.id === "x").map((f) => f.text)).toEqual(["b"]);
    expect(s.badge).toEqual({ provider: "local", model: "gemma4-26b-a4b", ms: 2800 });
  });
  it("tracks lines being checked for the shimmer", () => {
    const s = reduce(initialState, { kind: "server", msg: { type: "status", page_id: "p1", phase: "checking", line_ids: ["L1"] } });
    expect(s.checking["p1"]).toEqual(["L1"]);
    expect(reduce(s, { kind: "server", msg: { type: "status", page_id: "p1", phase: "waiting" } }).checking["p1"]).toEqual([]);
  });
});
