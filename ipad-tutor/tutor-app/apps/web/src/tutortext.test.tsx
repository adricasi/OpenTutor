// The tutor's LaTeX is typeset with KaTeX (TutorText); plain text and the student's ink are untouched, and model output
// can never become HTML.
import { act, type ReactElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it } from "vitest";
import type { FeedbackEvent, Reading } from "./protocol";
import { Popover, QuestionChips } from "./tutor/Overlays";
import { TutorLayer } from "./tutor/TutorLayer";
import { plainMath, splitMath, TutorText } from "./tutor/TutorText";
import type { PageIndex } from "./tutor/geometry";

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
});

describe("splitMath", () => {
  const kinds = (s: string) => splitMath(s).map((x) => (x.kind === "text" ? `T:${x.text}` : `${x.display ? "D" : "M"}:${x.tex}`));

  it("splits the four delimiters", () => {
    expect(kinds("so $\\frac{x^5}{3}$ is it")).toEqual(["T:so ", "M:\\frac{x^5}{3}", "T: is it"]);
    expect(kinds("$$\\int_0^1 x^3\\,dx$$")).toEqual(["D:\\int_0^1 x^3\\,dx"]);
    expect(kinds("try \\(x^3\\) then \\[x^4/4\\]")).toEqual(["T:try ", "M:x^3", "T: then ", "D:x^4/4"]);
    expect(kinds("$a$ and $b$")).toEqual(["M:a", "T: and ", "M:b"]);
  });

  it("keeps undelimited LaTeX, currency and unclosed delimiters as plain text", () => {
    expect(kinds("\\frac{x^5}{3} and x^2")).toEqual(["T:\\frac{x^5}{3} and x^2"]);
    expect(kinds("it costs $5 and $10")).toEqual(["T:it costs $5 and $10"]);
    expect(kinds("a lone $ sign")).toEqual(["T:a lone $ sign"]);
    expect(kinds("open \\( never closed")).toEqual(["T:open \\( never closed"]);
    expect(kinds("empty $$ $$ here")).toEqual(["T:empty $$ $$ here"]);
    expect(kinds("escaped \\$x\\$ dollars")).toEqual(["T:escaped $x$ dollars"]);
    expect(kinds("no\nmaths $across\nlines$")).toEqual(["T:no\nmaths $across\nlines$"]);
    expect(kinds("")).toEqual([]);
  });

  it("plainMath drops only the delimiters", () => {
    expect(plainMath("= $\\frac{x^4}{4}$ + C")).toBe("= \\frac{x^4}{4} + C");
    expect(plainMath("x^3 + 1")).toBe("x^3 + 1");
  });
});

describe("TutorText", () => {
  it("typesets delimited maths with KaTeX", () => {
    const el = render(<p><TutorText text="Integrate: $\frac{x^5}{3}$." /></p>);
    expect(el.querySelector(".tutor-math .katex")).not.toBeNull();
    expect(el.textContent).toContain("Integrate: ");
    expect(el.textContent).not.toContain("$");
  });

  it("leaves plain text exactly as it was", () => {
    const el = render(<p><TutorText text="Check the sign of the second term \frac{1}{2}." /></p>);
    expect(el.innerHTML).toBe("<p>Check the sign of the second term \\frac{1}{2}.</p>");
  });

  it("falls back to the raw text when KaTeX cannot parse it", () => {
    const el = render(<p><TutorText text="bad $\frac{1}{$ here" /></p>);
    expect(el.querySelector(".katex, .katex-error")).toBeNull();
    expect(el.textContent).toBe("bad $\\frac{1}{$ here");
  });

  it("never turns model output into HTML, inside or outside maths", () => {
    const evil = `<img src=x onerror="alert(1)"> and $<img src=x onerror="alert(2)">$ and \\(\\href{javascript:alert(3)}{x}\\) and $\\htmlClass{x}{y}$`;
    const el = render(<p><TutorText text={evil} /></p>);
    expect(el.querySelector("img")).toBeNull();
    expect(el.querySelector("[onerror]")).toBeNull();
    expect(el.querySelector("a")).toBeNull();
    expect(el.innerHTML).not.toContain("javascript:");
    expect(el.textContent).toContain('<img src=x onerror="alert(1)">');
  });
});

const reading: Reading = {
  page_id: "p1", version: 1,
  lines: [{ id: "L1", stroke_ids: ["s1"], bbox: { x: 40, y: 100, w: 200, h: 30 }, kind: "math", latex: "\\int x^4 dx", confidence: 0.9 }],
};
const index: PageIndex = { strokes: new Map(), reading };
const mark: FeedbackEvent = { id: "fb_m", page_id: "p1", anchor: { line_id: "L1" }, type: "mark", glyph: "underline", severity: "major", source: "tutor", text: "Check the power" };
const hint = (text: string): FeedbackEvent => ({ id: "fb_h", page_id: "p1", anchor: { line_id: "L1" }, type: "hint", hint_level: 1, source: "tutor", text });
const popover = (fb: FeedbackEvent[], target = mark) => (
  <Popover target={target} at={{ x: 100, y: 100 }} feedback={fb} pageWidth={1194} accepted={false}
    onHint={() => {}} onDismiss={() => {}} onGhost={() => {}} onClose={() => {}} />
);

describe("tutor text on the page", () => {
  it("a hint with $\\frac{x^5}{3}$ shows typeset maths in the popover", () => {
    const el = render(popover([hint("The antiderivative is $\\frac{x^5}{5}$, not $\\frac{x^5}{3}$.")]));
    const p = el.querySelector(".popover .hint p")!;
    expect(p.querySelectorAll(".katex")).toHaveLength(2);
    expect(p.textContent).not.toContain("$");
  });

  it("a plain hint is unchanged", () => {
    const el = render(popover([hint("Look at the exponent again.")]));
    expect(el.querySelector(".popover .hint p")!.innerHTML).toBe("Look at the exponent again.");
    expect(el.querySelector(".popover .mark-text")!.innerHTML).toBe("Check the power");
  });

  it("question chips and the popover's ghost text typeset too", () => {
    const q: FeedbackEvent = { id: "fb_q", page_id: "p1", anchor: { line_id: "L1" }, type: "question", source: "recognizer", text: "Is this $x^4$?" };
    let el = render(<QuestionChips feedback={[q]} index={index} onAnswer={() => {}} />);
    expect(el.querySelector(".chip .katex")).not.toBeNull();
    const ghost: FeedbackEvent = { id: "fb_g", page_id: "p1", anchor: { line_id: "L1" }, type: "ghost", source: "tutor", text: "$\\frac{x^5}{5} + C$" };
    el = render(popover([], ghost));
    expect(el.querySelector(".ghost-text .katex")).not.toBeNull();
  });

  it("plain ghost ink on the canvas stays handwriting (LaTeX ghosts are typeset: pagemath.test.tsx)", () => {
    const ghost: FeedbackEvent = { id: "fb_g", page_id: "p1", anchor: { line_id: "L1" }, type: "ghost", source: "tutor", text: "= x^5/5 + C" };
    const el = render(<TutorLayer page={{ id: "p1", index: 0, width: 800, height: 600 }} feedback={[ghost]} index={index} checking={[]} accepted={new Set()} onOpen={() => {}} />);
    const t = el.querySelector(".ghost text")!;
    expect(t.textContent).toBe("= x^5/5 + C");
    expect(t.getAttribute("font-family")).toContain("Caveat");
    expect(el.querySelector(".katex")).toBeNull();
  });
});
