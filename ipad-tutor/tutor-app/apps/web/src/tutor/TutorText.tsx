// The tutor's own text (hints, explanations, speak lines, chips, summaries) with its LaTeX typeset by KaTeX.
// Only explicitly delimited maths is typeset: $…$, $$…$$, \(…\) and \[…\]. A bare "\frac{a}{b}" stays plain text.
// Plain segments are React text nodes; only KaTeX's own output goes through dangerouslySetInnerHTML (KaTeX escapes
// its input, and `trust` stays off so \href/\htmlClass… cannot produce links or attributes). Never used for the
// student's ink, and never inside the SVG tutor layer (ghost ink there stays handwriting: see `plainMath`).
import { useMemo } from "react";
import katex from "katex";
import "katex/dist/katex.min.css";
import "./tutorText.css";

export type Segment =
  | { kind: "text"; text: string }
  | { kind: "math"; tex: string; display: boolean; raw: string };

const isSpace = (c: string | undefined) => c === undefined || /\s/.test(c);

/** Index of the `$` closing an inline `$…$` opened just before `start`, or -1. Pandoc's rule: the opening `$` is
 * followed by a non-space, the closing one preceded by a non-space and not followed by a digit (so "$5 and $10" is
 * text), and inline maths does not span lines. */
function inlineClose(src: string, start: number): number {
  if (isSpace(src[start]) || src[start] === "$") return -1;
  for (let j = start; j < src.length; j++) {
    const c = src[j];
    if (c === "\\") { j++; continue; }
    if (c === "\n") return -1;
    if (c === "$" && !isSpace(src[j - 1]) && !/[0-9]/.test(src[j + 1] ?? "")) return j;
  }
  return -1;
}

/** Splits tutor text into plain and maths segments. Unclosed or empty delimiters stay plain text; `\$` is a dollar. */
export function splitMath(src: string): Segment[] {
  const out: Segment[] = [];
  let buf = "";
  const flush = () => { if (buf) { out.push({ kind: "text", text: buf }); buf = ""; } };
  const math = (tex: string, display: boolean, raw: string) => { flush(); out.push({ kind: "math", tex, display, raw }); };
  let i = 0;
  while (i < src.length) {
    const c = src[i]!;
    const n = src[i + 1];
    if (c === "\\" && (n === "(" || n === "[")) {
      const close = n === "(" ? "\\)" : "\\]";
      const end = src.indexOf(close, i + 2);
      const tex = end < 0 ? "" : src.slice(i + 2, end);
      if (tex.trim()) { math(tex, n === "[", src.slice(i, end + 2)); i = end + 2; continue; }
      buf += c + n; i += 2; continue;
    }
    if (c === "\\" && (n === "$" || n === "\\")) { buf += n === "$" ? "$" : "\\\\"; i += 2; continue; }
    if (c === "$" && n === "$") {
      const end = src.indexOf("$$", i + 2);
      const tex = end < 0 ? "" : src.slice(i + 2, end);
      if (tex.trim()) { math(tex, true, src.slice(i, end + 2)); i = end + 2; continue; }
      buf += "$$"; i += 2; continue;
    }
    if (c === "$") {
      const end = inlineClose(src, i + 1);
      if (end > 0) { math(src.slice(i + 1, end), false, src.slice(i, end + 1)); i = end + 1; continue; }
    }
    buf += c; i++;
  }
  flush();
  return out;
}

/** KaTeX HTML for one segment, or null when it cannot be typeset (the caller then shows the raw text). */
export function renderMath(tex: string, display: boolean): string | null {
  try {
    const html = katex.renderToString(tex, { throwOnError: false, output: "html", displayMode: display, strict: "ignore", trust: false });
    // throwOnError:false renders a parse error as red source text; show it as the tutor's plain text instead
    return html.startsWith('<span class="katex-error"') ? null : html;
  } catch {
    return null;
  }
}

/** For places that must stay handwriting (ghost ink on the canvas): drop the delimiters, keep the characters. */
export function plainMath(src: string): string {
  return splitMath(src).map((s) => (s.kind === "text" ? s.text : s.tex)).join("");
}

export function TutorText({ text }: { text: string | null | undefined }) {
  const parts = useMemo(() => {
    if (!text) return [];
    try {
      return splitMath(text).map((s) => (s.kind === "text" ? s : { ...s, html: renderMath(s.tex, s.display) }));
    } catch {
      return [{ kind: "text" as const, text }];
    }
  }, [text]);
  return (
    <>
      {parts.map((s, i) =>
        s.kind === "text" ? s.text
          : s.html == null ? s.raw
          : <span key={i} className={s.display ? "tutor-math display" : "tutor-math"} dangerouslySetInnerHTML={{ __html: s.html }} />,
      )}
    </>
  );
}
