// The tutor's maths *on the page* (formulas and typeset ghost ink), set by KaTeX with the same safety as TutorText:
// `trust: false`, `throwOnError: false`, and a fallback to the raw text when KaTeX cannot parse it. Plain segments
// are React text nodes; only KaTeX's own output goes through dangerouslySetInnerHTML. Never used for the student's ink.
import { renderMath, splitMath } from "./TutorText";

type Part = { kind: "text"; text: string } | { kind: "math"; html: string };

/** Ghost text that should be typeset: it has $…$ / \(…\) delimiters or a backslash command (`\frac`, `\sqrt`…). */
export function looksLikeTex(text: string): boolean {
  return /\\[a-zA-Z]+/.test(text) || splitMath(text).some((s) => s.kind === "math");
}

/** The pieces to show. `whole`: the text is LaTeX without delimiters (a formula), set as one display-style run. */
export function pageMathParts(text: string, whole: boolean): Part[] {
  const delimited = !whole && splitMath(text).some((s) => s.kind === "math");
  if (!delimited) {
    const html = renderMath(`\\displaystyle ${text}`, false);
    return [html == null ? { kind: "text", text } : { kind: "math", html }];
  }
  return splitMath(text).map((s) => {
    if (s.kind === "text") return s;
    const html = renderMath(`\\displaystyle ${s.tex}`, false); // fractions at full size, inline with the text
    return html == null ? { kind: "text", text: s.raw } : { kind: "math", html };
  });
}

/** True when nothing could be typeset (the caller may then prefer its own plain rendering). */
export const allText = (parts: Part[]) => parts.every((p) => p.kind === "text");

/** A generous on-screen width estimate (px) for layout before the element is measured (and in tests). */
export function estimateWidth(text: string): number {
  const visible = text.replace(/\\(frac|dfrac|tfrac)/g, "").replace(/\\[a-zA-Z]+/g, "x").replace(/[{}^_$\\]/g, "");
  const fracs = (text.match(/\\[dt]?frac/g) ?? []).length;
  return Math.max(48, visible.length * 14 - fracs * 10 + 24);
}

export function PageMathContent({ parts }: { parts: Part[] }) {
  return (
    <>
      {parts.map((p, i) =>
        p.kind === "text" ? p.text : <span key={i} className="page-math-tex" dangerouslySetInnerHTML={{ __html: p.html }} />,
      )}
    </>
  );
}

const cache = new Map<string, Part[]>(); // TutorLayer re-renders on every stroke; typeset each text once

/** `pageMathParts`, cached, and never throwing (anything unexpected → the raw text). */
export function pageMath(text: string, whole: boolean): Part[] {
  const key = `${whole ? "F" : "G"}:${text}`;
  let parts = cache.get(key);
  if (!parts) {
    try {
      parts = pageMathParts(text, whole);
    } catch {
      parts = [{ kind: "text", text }];
    }
    if (cache.size > 200) cache.clear();
    cache.set(key, parts);
  }
  return parts;
}
