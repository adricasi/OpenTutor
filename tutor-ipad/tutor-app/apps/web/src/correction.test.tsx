// "Tell the tutor what you meant": the typed / dictated answer on every "?" (question chips, "?" mark popovers).
import { act, type ReactElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DirtyTracker } from "./ink/dirty";
import { InkSurface } from "./ink/surface";
import type { FeedbackEvent, InkUpdate, Reading } from "./protocol";
import { CorrectionField, type Dictation } from "./tutor/Correction";
import { Popover, QuestionChips } from "./tutor/Overlays";
import type { PageIndex } from "./tutor/geometry";

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const reading: Reading = {
  page_id: "p1", version: 1,
  lines: [{ id: "L1", stroke_ids: ["s1"], bbox: { x: 40, y: 100, w: 200, h: 30 }, kind: "math", latex: "2+2=5", confidence: 0.3 }],
};
const index: PageIndex = { strokes: new Map(), reading };
const question: FeedbackEvent = {
  id: "fb_q", page_id: "p1", anchor: { line_id: "L1" }, type: "question", source: "recognizer",
  text: "I read this as 2+2=5 — correct?", choices: ["Yes", "No"],
};
const qMark: FeedbackEvent = { id: "fb_m", page_id: "p1", anchor: { line_id: "L1" }, type: "mark", glyph: "question", severity: "minor", source: "tutor", text: "is this a 4?" };
const popoverProps = {
  at: { x: 100, y: 100 }, feedback: [], pageWidth: 1194, accepted: false,
  onHint: () => {}, onDismiss: () => {}, onGhost: () => {}, onClose: () => {},
};

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
});

const $ = <T extends Element>(sel: string, from: ParentNode = host!) => from.querySelector<T>(sel);
const button = (label: string, from: ParentNode = host!) =>
  [...from.querySelectorAll("button")].find((b) => b.textContent === label || b.getAttribute("aria-label") === label);
function type(input: HTMLInputElement, value: string) {
  const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
  act(() => { set.call(input, value); input.dispatchEvent(new Event("input", { bubbles: true })); });
}
const click = (el: Element | undefined | null) => act(() => { (el as HTMLElement).click(); });
const submit = (form: Element | null) =>
  act(() => { form!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })); });

describe("question chip: Something else…", () => {
  it("opens a 'What did you write?' field and sends the typed text for the chip's line", () => {
    const onCorrect = vi.fn();
    const onAnswer = vi.fn();
    render(<QuestionChips feedback={[question]} index={index} onAnswer={onAnswer} onCorrect={onCorrect} />);
    expect($("input")).toBeNull();
    click(button("Something else…"));
    const input = $<HTMLInputElement>("input")!;
    expect(input.getAttribute("aria-label")).toBe("What did you write?");
    expect($(".chip")!.className).toContain("correcting");
    expect(button("Send")!.disabled).toBe(true); // nothing typed yet
    type(input, "  2+2=4 ");
    submit($("form"));
    expect(onCorrect).toHaveBeenCalledWith(question, "2+2=4", "typed");
    expect(onAnswer).not.toHaveBeenCalled();
    expect($("input")).toBeNull(); // the field closes; the server retracts the chip
  });

  it("keeps the one-tap answers, and Cancel closes the field without sending", () => {
    const onCorrect = vi.fn();
    const onAnswer = vi.fn();
    render(<QuestionChips feedback={[question]} index={index} onAnswer={onAnswer} onCorrect={onCorrect} />);
    click(button("Yes"));
    expect(onAnswer).toHaveBeenCalledWith(question, "Yes");
    click(button("Something else…"));
    click(button("Cancel"));
    expect($("input")).toBeNull();
    expect(onCorrect).not.toHaveBeenCalled();
  });

  it("has no Something else… without a correction handler", () => {
    render(<QuestionChips feedback={[question]} index={index} onAnswer={() => {}} />);
    expect(button("Something else…")).toBeUndefined();
  });
});

describe("popover of a ? mark: Tell me what you meant", () => {
  it("shows the field on a question mark and sends what was typed", () => {
    const onCorrect = vi.fn();
    render(<Popover target={qMark} {...popoverProps} onCorrect={onCorrect} />);
    expect($(".correction-block .label")!.textContent).toBe("Tell me what you meant");
    const input = $<HTMLInputElement>("input")!;
    expect(document.activeElement).not.toBe(input); // opening a popover doesn't pop the iPad keyboard up
    type(input, "x squared plus three x");
    click(button("Send"));
    expect(onCorrect).toHaveBeenCalledWith("x squared plus three x", "typed");
  });

  it("is not offered on ✓/✗ marks or ghosts", () => {
    const onCorrect = vi.fn();
    render(<Popover target={{ ...qMark, glyph: "cross" }} {...popoverProps} onCorrect={onCorrect} />);
    expect($("input")).toBeNull();
    render(<Popover target={{ ...qMark, type: "ghost", glyph: null }} {...popoverProps} onCorrect={onCorrect} />);
    expect($("input")).toBeNull();
  });
});

describe("saying it instead of typing", () => {
  it("the mic asks for dictation, shows listening…, and the next transcript fills the field", () => {
    const onDictate = vi.fn();
    const onCorrect = vi.fn();
    const before = ["why is this wrong?"];
    const view = (transcripts: string[]) => {
      const dictation: Dictation = { transcripts, onDictate };
      return <Popover target={qMark} {...popoverProps} onCorrect={onCorrect} dictation={dictation} />;
    };
    render(view(before));
    click(button("Say it"));
    expect(onDictate).toHaveBeenCalledWith("L1", false);
    const input = $<HTMLInputElement>("input")!;
    expect(input.placeholder).toBe("listening…");
    render(view(before)); // re-render without a new utterance: still listening
    expect(input.value).toBe("");
    render(view([...before, "two plus two equals four"])); // the next voice.transcript
    expect(input.value).toBe("two plus two equals four");
    expect(input.placeholder).toBe("Tell me what you meant");
    type(input, "two plus two equals four!"); // editable before sending
    click(button("Send"));
    expect(onCorrect).toHaveBeenCalledWith("two plus two equals four!", "spoken");
    expect(onDictate).toHaveBeenCalledTimes(1); // nothing to cancel: the dictation was used
  });

  it("no mic without voice; closing the field while listening cancels the dictation", () => {
    render(<Popover target={qMark} {...popoverProps} onCorrect={() => {}} />);
    expect(button("Say it")).toBeUndefined();
    const onDictate = vi.fn();
    render(<Popover target={qMark} {...popoverProps} onCorrect={() => {}} dictation={{ transcripts: [], onDictate }} />);
    click(button("Say it"));
    render(<Popover target={{ ...qMark, glyph: "check" }} {...popoverProps} onCorrect={() => {}} dictation={{ transcripts: [], onDictate }} />);
    expect(onDictate).toHaveBeenLastCalledWith("L1", true);
  });
});

describe("the field and the pen", () => {
  it("pointer input on the field never draws, and isn't default-prevented (keyboard, selection, Scribble)", () => {
    const ctx = new Proxy({}, { get: () => () => undefined, set: () => true });
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(ctx as unknown as CanvasRenderingContext2D);
    const sent: InkUpdate[] = [];
    const tracker = new DirtyTracker((m) => sent.push(m), 700);
    const pageEl = render(
      <div className="page">
        <canvas className="ink" />
        <CorrectionField lineId="L1" label="What did you write?" onSend={() => {}} />
      </div>,
    );
    const canvas = $<HTMLCanvasElement>("canvas", pageEl)!;
    const surface = new InkSurface({ canvas, tracker, t0: performance.now() });
    const pageListener = vi.fn();
    $(".page", pageEl)!.addEventListener("pointerdown", pageListener); // a native listener above the field
    const pen = (target: Element, kind: string, x: number) => {
      const e = new PointerEvent(kind, { pointerId: 1, pointerType: "pen", bubbles: true, cancelable: true, clientX: x, clientY: 20, pressure: 0.4 });
      act(() => { target.dispatchEvent(e); });
      return e;
    };
    const input = $<HTMLInputElement>("input", pageEl)!;
    const down = pen(input, "pointerdown", 10);
    pen(input, "pointermove", 40);
    pen(input, "pointerup", 60);
    expect(surface.strokes).toHaveLength(0);
    expect(down.defaultPrevented).toBe(false);
    expect(pageListener).not.toHaveBeenCalled(); // stopped at the field

    pen(canvas, "pointerdown", 10); // control: the same pen on the canvas draws
    pen(canvas, "pointermove", 40);
    pen(canvas, "pointerup", 60);
    expect(surface.strokes).toHaveLength(1);
    surface.dispose();
  });

  it("typed text goes to the field (keyboard / Scribble insert text like typing)", () => {
    const onSend = vi.fn();
    render(<CorrectionField lineId="L1" label="What did you write?" onSend={onSend} />);
    const input = $<HTMLInputElement>("input")!;
    expect(input.getAttribute("enterkeyhint")).toBe("send");
    expect(input.getAttribute("autocorrect")).toBe("off"); // what the student wrote, not what autocorrect prefers
    type(input, "I has a cat");
    submit($("form"));
    expect(onSend).toHaveBeenCalledWith("I has a cat", "typed");
  });
});
