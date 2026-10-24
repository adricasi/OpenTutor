// The tutor's layer: marks and ghost ink on top of the user's ink, never mixed into it.
// Locked for drawing (pointer events only on the marks themselves, so the pen still writes everywhere else).
// Typeset maths (formulas, LaTeX ghost ink) is HTML in a sibling overlay aligned with the SVG (like the question
// chips), not a <foreignObject>: KaTeX lays out with position:relative spans, which WebKit misplaces in foreignObject.
import { useState, type ReactNode } from "react";
import type { FeedbackEvent, PageRef } from "../protocol";
import type { Box } from "../ink/draw";
import { anchorBox, diagramGhostPositions, ghostPosition, glyphPosition, type PageIndex } from "./geometry";
import { allText, estimateWidth, looksLikeTex, PageMathContent, pageMath } from "./PageMath";
import { plainMath } from "./TutorText";

export const TUTOR = { ok: "#1a7f37", minor: "#b26b00", major: "#c0392b", ghost: "#5b4bdb", pin: "#5b4bdb" };

interface Props {
  page: PageRef;
  feedback: FeedbackEvent[];
  index: PageIndex;
  checking: string[];
  accepted: Set<string>;
  onOpen: (f: FeedbackEvent, at: { x: number; y: number }) => void;
  /** Photo pages: the page is shown smaller/larger than its pixel size; marks keep their on-screen size. */
  display?: { w: number; h: number };
}

const scaleAt = (x: number, y: number, u: number) => (u === 1 ? undefined : `translate(${x} ${y}) scale(${u}) translate(${-x} ${-y})`);

function color(f: FeedbackEvent): string {
  if (f.glyph === "pin") return TUTOR.pin;
  if (f.severity === "ok" || f.glyph === "check") return TUTOR.ok;
  if (f.severity === "minor") return TUTOR.minor;
  return TUTOR.major;
}

function Glyph({ f, x, y, n }: { f: FeedbackEvent; x: number; y: number; n: number }) {
  const c = color(f);
  switch (f.glyph) {
    case "check":
      return <path d={`M${x - 9} ${y} l6 7 l12 -15`} stroke={c} strokeWidth={3} fill="none" strokeLinecap="round" />;
    case "cross":
      return (
        <g stroke={c} strokeWidth={3} strokeLinecap="round">
          <line x1={x - 8} y1={y - 8} x2={x + 8} y2={y + 8} />
          <line x1={x + 8} y1={y - 8} x2={x - 8} y2={y + 8} />
        </g>
      );
    case "pin":
      return (
        <g>
          <circle cx={x} cy={y} r={11} fill={c} />
          <text x={x} y={y + 4.5} textAnchor="middle" fontSize={13} fontWeight={700} fill="#fff">{n}</text>
        </g>
      );
    default: // question (and anything unknown) → a "?"
      return <text x={x} y={y + 8} textAnchor="middle" fontSize={24} fontWeight={700} fill={c} fontFamily="Caveat, cursive">?</text>;
  }
}

function underlinePath(b: Box, squiggle: boolean): string {
  const y = b.y + b.h + 5;
  if (!squiggle) return `M${b.x - 2} ${y} L${b.x + b.w + 2} ${y}`;
  let d = `M${b.x - 2} ${y}`;
  for (let x = b.x - 2, up = true; x < b.x + b.w + 2; x += 6, up = !up) d += ` q3 ${up ? -4 : 4} 6 0`;
  return d;
}

type Open = (f: FeedbackEvent, at: { x: number; y: number }) => void;

/** A tutor formula: to the right of its line (clear of the ✓/✗ margin glyph), or below it when it doesn't fit.
 * Positions are page units; the element keeps its on-screen size (1 page unit = 1/u px). */
function PageFormula({ f, box, page, u, onOpen }: { f: FeedbackEvent; box: Box; page: PageRef; u: number; onOpen: Open }) {
  const text = f.text ?? "";
  const [size, setSize] = useState<{ text: string; w: number; h: number } | null>(null);
  const measured = size?.text === text ? size : null;
  const measure = (el: HTMLDivElement | null) => {
    if (!el || !el.offsetWidth) return; // not laid out (tests): keep the estimate
    if (el.offsetWidth !== measured?.w || el.offsetHeight !== measured?.h) setSize({ text, w: el.offsetWidth, h: el.offsetHeight });
  };
  const w = (measured?.w ?? estimateWidth(text)) * u;
  const h = (measured?.h ?? 44) * u;
  const rightX = box.x + box.w + 52 * u;
  const right = rightX + w <= page.width - 8 * u;
  const x = right ? rightX : Math.max(8 * u, Math.min(box.x, page.width - 8 * u - w));
  const y = right ? box.y + box.h / 2 - h / 2 : box.y + box.h + 10 * u;
  return (
    <div ref={measure} className="page-math formula" data-feedback={f.id} data-place={right ? "right" : "below"}
      role="button" aria-label="Tutor's formula" style={{ left: x / u, top: y / u }}
      onPointerUp={() => onOpen(f, { x: x + w / 2, y: y + h })}>
      <PageMathContent parts={pageMath(text, true)} />
    </div>
  );
}

export function TutorLayer({ page, feedback, index, checking, accepted, onOpen, display }: Props) {
  let pinNo = 0;
  const u = display ? page.width / display.w : 1; // page units per screen pixel
  const diagramGhosts = diagramGhostPositions(feedback.filter((f) => f.type === "ghost"), index, page.height);
  const html: ReactNode[] = []; // typeset maths, drawn in the HTML overlay
  const items = feedback.map((f) => {
    const box = anchorBox(f.anchor, index);
    if (!box) return null; // unknown anchor → not drawn (the server drops these too)
    const open = (x: number, y: number) => () => onOpen(f, { x, y });
    if (f.type === "formula" && f.text) {
      html.push(<PageFormula key={f.id} f={f} box={box} page={page} u={u} onOpen={onOpen} />);
      return null;
    }
    if (f.type === "mark") {
      if (f.glyph === "underline" || f.glyph === "circle") {
        const c = color(f);
        const shape = f.glyph === "circle"
          ? <ellipse cx={box.x + box.w / 2} cy={box.y + box.h / 2} rx={box.w / 2 + 12 * u} ry={box.h / 2 + 10 * u} stroke={c} strokeWidth={2.5 * u} fill="none" />
          : <path d={underlinePath(box, f.severity === "minor")} stroke={c} strokeWidth={2.5 * u} fill="none" strokeLinecap="round" />;
        return (
          <g key={f.id} className="mark" data-feedback={f.id} onPointerUp={open(box.x + box.w / 2, box.y + box.h + 12)}>
            {shape}
            <rect x={box.x - 6} y={box.y + box.h - 4} width={box.w + 12} height={20} fill="transparent" />
          </g>
        );
      }
      const n = f.glyph === "pin" ? ++pinNo : 0;
      const p = glyphPosition(f, box, page.width);
      return (
        <g key={f.id} className="mark" data-feedback={f.id} data-glyph={f.glyph ?? ""} onPointerUp={open(p.x, p.y)}
          transform={scaleAt(p.x, p.y, u)}>
          <Glyph f={f} x={p.x} y={p.y} n={n} />
          <circle cx={p.x} cy={p.y} r={20} fill="transparent" />
        </g>
      );
    }
    if (f.type === "ghost" && f.text) {
      const p = diagramGhosts.get(f.id) ?? ghostPosition(box, page.height);
      const solid = accepted.has(f.id);
      const branch = diagramGhosts.has(f.id);
      // LaTeX ghosts are typeset (HTML overlay; the element itself is the hit area, so it matches the rendered size);
      // plain ones, and LaTeX KaTeX can't parse, stay handwriting
      const tex = looksLikeTex(f.text) ? pageMath(f.text, false) : null;
      if (tex && !allText(tex)) {
        html.push(
          <div key={f.id} className={`page-math ghost-tex${solid ? " accepted" : ""}`} data-feedback={f.id} role="button"
            aria-label="Tutor's suggestion" style={{ left: p.x / u - 4, top: p.y / u - 8 }} onPointerUp={open(p.x, p.y)}>
            <PageMathContent parts={tex} />
          </div>,
        );
        return (
          <g key={f.id} className={`ghost${solid ? " accepted" : ""}`} data-feedback={f.id} onPointerUp={open(p.x, p.y)}
            transform={scaleAt(p.x, p.y, u)}>
            {!solid && !branch && <line x1={p.x - 8} y1={p.y - 22} x2={p.x - 8} y2={p.y + 6} stroke={TUTOR.ghost} strokeWidth={2} strokeDasharray="4 4" />}
            {branch && <line x1={p.x - 36} y1={p.y - 8} x2={p.x - 8} y2={p.y - 8} stroke={TUTOR.ghost} strokeWidth={2} strokeDasharray="5 4" />}
          </g>
        );
      }
      const hand = plainMath(f.text); // handwriting: "$x^3$" → "x^3"
      return (
        <g key={f.id} className={`ghost${solid ? " accepted" : ""}`} data-feedback={f.id} onPointerUp={open(p.x, p.y)}
          transform={scaleAt(p.x, p.y, u)}>
          <text x={p.x} y={p.y} fontFamily="Caveat, cursive" fontSize={30} fill={TUTOR.ghost} opacity={solid ? 1 : 0.72}>
            {hand}
          </text>
          {!solid && !branch && <line x1={p.x - 8} y1={p.y - 22} x2={p.x - 8} y2={p.y + 6} stroke={TUTOR.ghost} strokeWidth={2} strokeDasharray="4 4" />}
          {branch && <line x1={p.x - 36} y1={p.y - 8} x2={p.x - 8} y2={p.y - 8} stroke={TUTOR.ghost} strokeWidth={2} strokeDasharray="5 4" />}
          <rect x={p.x - 10} y={p.y - 30} width={Math.max(60, hand.length * 13)} height={40} fill="transparent" />
        </g>
      );
    }
    return null;
  });

  const shimmer = checking.flatMap((id) => {
    const b = anchorBox({ line_id: id }, index);
    return b ? [<rect key={`sh-${id}`} className="shimmer" x={b.x} y={b.y + b.h + 3} width={b.w} height={3} rx={1.5} />] : [];
  });

  const w = display?.w ?? page.width;
  const h = display?.h ?? page.height;
  return (
    <>
      <svg className="tutor-layer" width={w} height={h} viewBox={`0 0 ${page.width} ${page.height}`}>
        {shimmer}
        {items}
      </svg>
      {html.length > 0 && <div className="tutor-html" style={{ width: w, height: h }}>{html}</div>}
    </>
  );
}
