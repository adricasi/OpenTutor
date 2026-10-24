// The tutor's maths on the page is typeset (KaTeX): `formula` feedback and LaTeX ghost ink, in the tutor's HTML overlay
// over the canvas. Plain ghost ink stays handwriting; the student's ink is never touched; model output never becomes HTML.
import { act, type ReactElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import type { FeedbackEvent, PageRef, Reading, ServerMessage, Session } from "./protocol";
import { TutorLayer } from "./tutor/TutorLayer";
import type { PageIndex } from "./tutor/geometry";
import { looksLikeTex } from "./tutor/PageMath";

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let root: Root | null = null;
let host: HTMLDivElement | null = null;
function render(el: ReactElement): HTMLDivElement {
  if (!host) {
    host = document.createElement("div");
    document.body.appendChild(host);
    root = createRoot(host);
  }
  act(() => root!.render(el));
  return host;
}
afterEach(() => {
  act(() => root?.unmount());
  host?.remove();
  root = null; host = null;
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

const page: PageRef = { id: "p1", index: 0, width: 800, height: 600 };
const reading = (pageId = "p1"): Reading => ({
  page_id: pageId, version: 1,
  lines: [
    { id: "L1", stroke_ids: ["s1"], bbox: { x: 40, y: 100, w: 200, h: 30 }, kind: "math", latex: "\\int x^3 dx", confidence: 0.9 },
    { id: "L2", stroke_ids: ["s2"], bbox: { x: 40, y: 200, w: 700, h: 30 }, kind: "math", latex: "a+b", confidence: 0.9 }, // a full-width line
  ],
});
const index: PageIndex = { strokes: new Map(), reading: reading() };
const formula = (text: string, line = "L1", id = "fb_f"): FeedbackEvent =>
  ({ id, page_id: "p1", anchor: { line_id: line }, type: "formula", source: "tutor", text });
const ghost = (text: string): FeedbackEvent => ({ id: "fb_g", page_id: "p1", anchor: { line_id: "L1" }, type: "ghost", source: "tutor", text });
const layer = (feedback: FeedbackEvent[], onOpen = () => {}) => (
  <TutorLayer page={page} feedback={feedback} index={index} checking={[]} accepted={new Set()} onOpen={onOpen} />
);
const pointerUp = (el: Element) => act(() => { el.dispatchEvent(new PointerEvent("pointerup", { bubbles: true, pointerType: "pen" })); });
const px = (v: string) => Number(v.replace("px", ""));

describe("formula feedback on the page", () => {
  it("is typeset with KaTeX in the tutor overlay, to the right of its line", () => {
    const el = render(layer([formula("\\int_0^1 x^3\\,dx = \\frac{1}{4}")]));
    const f = el.querySelector<HTMLElement>(".tutor-html .page-math.formula[data-feedback=fb_f]")!;
    expect(f.querySelector(".katex")).not.toBeNull();
    expect(f.querySelector(".katex-error")).toBeNull();
    expect(f.dataset.place).toBe("right");
    const left = px(f.style.left), top = px(f.style.top);
    expect(left).toBeGreaterThan(40 + 200); // right of the line's end, clear of the ✓/✗ margin glyph
    expect(left).toBeLessThan(40 + 200 + 80);
    expect(top).toBeGreaterThan(100 - 40); // vertically on the line
    expect(top).toBeLessThan(100 + 30);
    expect(el.querySelector(".tutor-layer .katex")).toBeNull(); // not inside the SVG
  });

  it("goes below the line when there is no room on the right", () => {
    const el = render(layer([formula("x^2 + 2x + 1 = (x+1)^2", "L2")]));
    const f = el.querySelector<HTMLElement>(".page-math.formula")!;
    expect(f.dataset.place).toBe("below");
    expect(px(f.style.top)).toBeGreaterThanOrEqual(200 + 30);
    expect(px(f.style.left)).toBeLessThan(800);
  });

  it("keeps its on-screen size and position on a scaled photo page", () => {
    const el = render(<TutorLayer page={page} feedback={[formula("x^2")]} index={index} checking={[]} accepted={new Set()}
      onOpen={() => {}} display={{ w: 400, h: 300 }} />);
    const f = el.querySelector<HTMLElement>(".page-math.formula")!;
    expect(px(f.style.left)).toBeGreaterThan(120); // (40 + 200) / 2 screen px, + a 52 px gap that doesn't shrink
    expect(px(f.style.left)).toBeLessThan(180);
    expect(el.querySelector<HTMLElement>(".tutor-html")!.style.width).toBe("400px");
  });

  it("is not drawn for an unknown line", () => {
    const el = render(layer([formula("x^2", "nope")]));
    expect(el.querySelector(".page-math")).toBeNull();
  });

  it("tapping opens the popover at the formula", () => {
    const onOpen = vi.fn();
    const f = formula("\\frac{x^4}{4}");
    const el = render(layer([f], onOpen));
    pointerUp(el.querySelector(".page-math.formula")!);
    expect(onOpen).toHaveBeenCalledTimes(1);
    expect(onOpen.mock.calls[0]![0]).toBe(f);
    expect(onOpen.mock.calls[0]![1].x).toBeGreaterThan(240);
  });
});

describe("ghost ink", () => {
  it("with $\\frac{x^4}{4}$ is typeset, and keeps its hit area and dashed connector", () => {
    const onOpen = vi.fn();
    const g = ghost("= $\\frac{x^4}{4}$ + C");
    const el = render(layer([g], onOpen));
    const card = el.querySelector<HTMLElement>(".tutor-html .page-math.ghost-tex[data-feedback=fb_g]")!;
    expect(card.querySelector(".katex .mfrac")).not.toBeNull();
    expect(card.textContent).toContain("= ");
    expect(card.textContent).toContain(" + C");
    expect(card.textContent).not.toContain("$");
    expect(el.querySelector(".ghost text")).toBeNull(); // no Caveat copy underneath
    expect(el.querySelector("g.ghost line")).not.toBeNull();
    pointerUp(card); // the card itself is the hit area: it is exactly the rendered size
    expect(onOpen).toHaveBeenCalledWith(g, expect.objectContaining({ x: 40 }));
  });

  it("with an undelimited \\frac is typeset too", () => {
    const el = render(layer([ghost("\\frac{x^4}{4} + C")]));
    expect(el.querySelector(".ghost-tex .katex")).not.toBeNull();
  });

  it("in plain text keeps the handwriting font", () => {
    const el = render(layer([ghost("x^4/4 + C")]));
    const t = el.querySelector(".ghost text")!;
    expect(t.textContent).toBe("x^4/4 + C");
    expect(t.getAttribute("font-family")).toContain("Caveat");
    expect(el.querySelector(".katex, .page-math")).toBeNull();
  });

  it("that KaTeX cannot parse falls back to handwriting", () => {
    const el = render(layer([ghost("\\frac{x^4}{")]));
    expect(el.querySelector(".katex, .katex-error, .page-math")).toBeNull();
    expect(el.querySelector(".ghost text")!.textContent).toBe("\\frac{x^4}{");
  });

  it("looksLikeTex: delimiters or backslash commands", () => {
    expect(looksLikeTex("$x^2$")).toBe(true);
    expect(looksLikeTex("\\sqrt{2}")).toBe(true);
    expect(looksLikeTex("x^2 + 1")).toBe(false);
    expect(looksLikeTex("it costs $5 and $10")).toBe(false);
  });
});

describe("the on-page renderer never turns model output into HTML", () => {
  const evil = `<img src=x onerror="alert(1)"> \\href{javascript:alert(3)}{x} \\htmlClass{x}{y} \\url{javascript:alert(4)}`;
  it.each([
    ["formula", formula(evil)],
    ["formula with markup", formula(`<script>alert(1)</script>\\frac{1}{2}`)],
    ["ghost", ghost(`<img src=x onerror="alert(1)"> and $<img src=x onerror="alert(2)">$ and \\(\\href{javascript:alert(3)}{x}\\)`)],
    ["undelimited ghost", ghost(evil)],
  ])("%s", (_name, f) => {
    const el = render(layer([f]));
    expect(el.querySelector("img, script, a, [onerror]")).toBeNull();
    expect(el.innerHTML).not.toContain("javascript:");
    expect(el.querySelector(".page-math, .ghost text")).not.toBeNull(); // still shown, as text or maths
  });
});

// ---------------------------------------------------------------- the app: tap → Dismiss → mark.dismiss; retract
class FakeSocket {
  static OPEN = 1;
  static all: FakeSocket[] = [];
  readyState = 0;
  sent: string[] = [];
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onmessage: ((e: { data: string }) => void) | null = null;
  constructor() { FakeSocket.all.push(this); setTimeout(() => { this.readyState = 1; this.onopen?.(); }, 0); }
  send(d: string) { this.sent.push(d); }
  close() { this.readyState = 3; this.onclose?.(); }
  server(m: ServerMessage) { act(() => { this.onmessage?.({ data: JSON.stringify(m) }); }); }
}

describe("formula in the app", () => {
  it("tap → Dismiss sends mark.dismiss with its id; a retract removes it", async () => {
    FakeSocket.all = [];
    vi.stubGlobal("WebSocket", FakeSocket);
    vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} });
    const ctx = new Proxy({}, { get: () => () => undefined, set: () => true });
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(ctx as unknown as CanvasRenderingContext2D);
    const session = { id: "s1", mode: "math", problem: "", provider: "local", voice: false, pages: [] } as unknown as Session;
    const el = render(<App initial={session} />);
    await act(async () => { await new Promise((r) => setTimeout(r, 5)); });
    const ws = FakeSocket.all[0]!;
    const pageId = (window as unknown as { inkTutor: { surface: { page: PageRef } } }).inkTutor.surface.page.id;
    ws.server({ type: "reading", reading: reading(pageId) } as ServerMessage);
    const f = { ...formula("\\frac{x^4}{4}"), page_id: pageId };
    ws.server({ type: "feedback", feedback: f } as ServerMessage);
    const card = el.querySelector(".page .tutor-html .page-math.formula[data-feedback=fb_f]")!;
    expect(card.querySelector(".katex")).not.toBeNull();

    pointerUp(card);
    const dismiss = [...el.querySelectorAll<HTMLButtonElement>(".popover button")].find((b) => b.textContent === "Dismiss")!;
    expect(dismiss).toBeDefined();
    act(() => dismiss.click());
    const sent = ws.sent.map((s) => JSON.parse(s) as { type: string; feedback_id?: string; page_id?: string });
    expect(sent).toContainEqual({ type: "mark.dismiss", page_id: pageId, feedback_id: "fb_f" });
    expect(el.querySelector(".page-math.formula")).toBeNull(); // gone at once (locally dismissed)
    expect(el.querySelector(".popover")).toBeNull();

    // a second formula, retracted by the server
    ws.server({ type: "feedback", feedback: { ...f, id: "fb_f2" } } as ServerMessage);
    expect(el.querySelector(".page-math.formula[data-feedback=fb_f2]")).not.toBeNull();
    ws.server({ type: "feedback", feedback: { id: "fb_r", page_id: pageId, anchor: { line_id: "L1" }, type: "retract", refers_to: "fb_f2", source: "tutor" } } as ServerMessage);
    expect(el.querySelector(".page-math.formula")).toBeNull();
  });
});
