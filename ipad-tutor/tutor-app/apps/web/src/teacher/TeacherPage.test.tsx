import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { FeedbackEvent, HomeworkProgress, StudentRow, Stroke } from "../protocol";
import { HomeworkLink, StudentTable, TeacherPage } from "./TeacherPage";
import { marksAt } from "./SessionView";

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const summary = (text: string) => ({ session_id: "s", text, source: "template", log_hash: "h", created: 0 });
const ROWS: StudentRow[] = [
  {
    student: "Marta", status: "done", session_id: "s_marta", sessions: ["s_marta"], time_on_task_s: 420, hints: 2,
    hints_explicit: 0, mistakes: 1, mistakes_fixed: 1, summary: summary("Marta practised maths for about 7 minutes."),
  },
  {
    student: "Pau", status: "in_progress", session_id: "s_pau", sessions: ["s_pau1", "s_pau"], time_on_task_s: 30, hints: 3,
    hints_explicit: 1, mistakes: 2, mistakes_fixed: 0, summary: summary("Pau practised maths for under a minute."),
  },
  { student: "Joan", status: "not_started", session_id: "s_joan", time_on_task_s: 0, hints: 0, mistakes: 0, summary: null },
];
const HW = { id: "hw_1", title: "Definite integrals", mode: "math" as const, problem: "∫₀¹ x³ dx", created: 1, due: "2026-10-30" };
const PROGRESS: HomeworkProgress = {
  homework: HW, link: "http://192.168.1.23:8000/?hw=hw_1", qr: "data:image/svg+xml,qr", rows: ROWS,
};

let root: Root;
let el: HTMLDivElement;

beforeEach(() => {
  el = document.createElement("div");
  document.body.append(el);
  root = createRoot(el);
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    if (url === "/api/teacher/homework") return new Response(JSON.stringify([{ homework: HW, students: 3, done: 1 }]));
    if (url === "/api/teacher/homework/hw_1") return new Response(JSON.stringify(PROGRESS));
    if (url === "/api/teacher/sessions") return new Response("[]");
    return new Response("{}", { status: 404 });
  }));
});
afterEach(() => {
  act(() => root.unmount());
  el.remove();
  vi.unstubAllGlobals();
});

const flush = () => act(async () => { await new Promise((r) => setTimeout(r, 0)); });
const cells = (tr: Element) => [...tr.querySelectorAll("td")].map((td) => td.textContent);

describe("teacher table", () => {
  it("shows who did it, time, hints, mistakes fixed and the summary", async () => {
    const onOpen = vi.fn();
    await act(async () => root.render(<StudentTable rows={ROWS} onOpen={onOpen} />));
    const rows = [...el.querySelectorAll("tbody tr")];
    expect(rows.map((r) => r.getAttribute("data-status"))).toEqual(["done", "in_progress", "not_started"]);
    expect(cells(rows[0]!)).toEqual(["Marta", "✓ done", "7 min", "2", "1 of 1", "Marta practised maths for about 7 minutes."]);
    expect(cells(rows[1]!)).toEqual(["Pau · 2 tries", "in progress", "< 1 min", "3 (solution ×1)", "0 of 2", "Pau practised maths for under a minute."]);
    expect(cells(rows[2]!)).toEqual(["Joan", "not started", "–", "0", "–", ""]);
    act(() => { (rows[1] as HTMLElement).click(); });
    expect(onOpen).toHaveBeenCalledWith("s_pau"); // opens that student's session, read-only
  });

  it("says when nobody has opened the homework yet", async () => {
    await act(async () => root.render(<StudentTable rows={[]} onOpen={() => {}} />));
    expect(el.querySelector("[data-testid=no-students]")!.textContent).toContain("Nobody");
  });
});

describe("homework link and QR", () => {
  it("shows the QR and the link students open", async () => {
    await act(async () => root.render(<HomeworkLink progress={PROGRESS} />));
    const box = el.querySelector("[data-testid=homework-link]")!;
    expect(box.querySelector("img")!.getAttribute("src")).toBe("data:image/svg+xml,qr");
    const a = box.querySelector("a")!;
    expect(a.getAttribute("href")).toBe("http://192.168.1.23:8000/?hw=hw_1");
    expect(box.textContent).toContain("make pair"); // a LAN link: how to get the tunnel one
  });

  it("names the tunnel link as such", async () => {
    const tunnel = { ...PROGRESS, link: "https://lemon-tiger.trycloudflare.com/?hw=hw_1&k=T0k" };
    await act(async () => root.render(<HomeworkLink progress={tunnel} />));
    expect(el.querySelector("[data-testid=homework-link]")!.textContent).toContain("HTTPS tunnel link");
  });
});

describe("teacher page", () => {
  it("loads the homework list, opens the first one with its link, table and CSV export", async () => {
    await act(async () => root.render(<TeacherPage pollMs={60_000} />));
    await flush();
    await flush();
    expect(el.querySelector(".teacher-list")!.textContent).toContain("Definite integrals1/3");
    expect(el.querySelector("h2")!.textContent).toBe("Definite integrals");
    expect(el.querySelector("[data-testid=homework-link] img")).not.toBeNull();
    expect(el.querySelectorAll("[data-testid=student-table] tbody tr")).toHaveLength(3);
    const csv = [...el.querySelectorAll("a")].find((a) => a.textContent === "Export CSV")!;
    expect(csv.getAttribute("href")).toBe("/api/teacher/export.csv?homework=hw_1");
    expect(el.querySelector("[data-testid=new-homework]")).not.toBeNull();
  });

  it("explains a refusal (opened through the tunnel without the teacher link)", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 403 })));
    await act(async () => root.render(<TeacherPage />));
    await flush();
    expect(el.querySelector("[role=alert]")!.textContent).toContain("only opens on the Mac");
  });
});

describe("read-only replay", () => {
  const st = (id: string, t: number): Stroke => ({ id, page_id: "p", pts: [[t, 0, 0, 0.3, 0, 0]] });
  const mark = (id: string, line: string): FeedbackEvent => ({ id, page_id: "p", anchor: { line_id: line }, type: "mark", glyph: "check" });
  it("shows a line's marks once all of its strokes are drawn", () => {
    const order = [st("a", 0), st("b", 10), st("c", 20)];
    const lines = new Map([["L_a", ["a", "b"]], ["L_c", ["c"]]]);
    const fb = [mark("m1", "L_a"), mark("m2", "L_c")];
    expect(marksAt(fb, order, 1, lines).map((f) => f.id)).toEqual([]);
    expect(marksAt(fb, order, 2, lines).map((f) => f.id)).toEqual(["m1"]);
    expect(marksAt(fb, order, 3, lines).map((f) => f.id)).toEqual(["m1", "m2"]);
  });
});
