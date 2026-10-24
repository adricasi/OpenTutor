import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { HomeworkPublic, Session } from "../protocol";
import { HomeworkGate, HomeworkStart } from "./HomeworkStart";
import { NAME_KEY } from "./api";

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const HW: HomeworkPublic = { id: "hw_1", title: "Definite integrals", mode: "math", problem: "Compute ∫₀¹ x³ dx.", due: "2026-10-30" };
const SESSION: Session = { id: "sess_1", mode: "math", problem: HW.problem, student: "Marta", homework_id: "hw_1" };

let root: Root;
let el: HTMLDivElement;
let calls: { url: string; init?: RequestInit }[];

beforeEach(() => {
  el = document.createElement("div");
  document.body.append(el);
  root = createRoot(el);
  calls = [];
  window.localStorage.clear();
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    if (url === "/api/homework/hw_1") return new Response(JSON.stringify(HW));
    if (url === "/api/sessions") return new Response(JSON.stringify(SESSION));
    return new Response("{}", { status: 404 });
  }));
});
afterEach(() => {
  act(() => root.unmount());
  el.remove();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const flush = () => act(async () => { await new Promise((r) => setTimeout(r, 0)); });

function type(input: HTMLInputElement, value: string) {
  const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
  act(() => { set.call(input, value); input.dispatchEvent(new Event("input", { bubbles: true })); });
}

async function render(onStart = vi.fn(), hwId = "hw_1") {
  await act(async () => root.render(<HomeworkStart hwId={hwId} onStart={onStart} />));
  await flush();
  return onStart;
}

describe("homework start screen", () => {
  it("shows the homework and asks for the first name before starting", async () => {
    const onStart = await render();
    expect(el.querySelector("[data-testid=homework-title]")!.textContent).toBe("Definite integrals");
    expect(el.querySelector("[data-testid=homework-problem]")!.textContent).toBe("Compute ∫₀¹ x³ dx.");
    expect(el.textContent).toContain("Due 2026-10-30");
    const button = el.querySelector<HTMLButtonElement>("button[type=submit]")!;
    expect(button.disabled).toBe(true); // no name yet
    type(el.querySelector<HTMLInputElement>("[data-testid=student-name]")!, "  Marta  ");
    expect(button.disabled).toBe(false);
    await act(async () => { el.querySelector("form")!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })); });
    await flush();
    const post = calls.find((c) => c.url === "/api/sessions")!;
    expect(JSON.parse(String(post.init!.body))).toEqual({ mode: "math", problem: HW.problem, student: "Marta", homework_id: "hw_1" });
    expect(onStart).toHaveBeenCalledWith(SESSION);
    expect(window.localStorage.getItem(NAME_KEY)).toBe("Marta"); // remembered on this device
  });

  it("pre-fills the remembered name, which the student can change", async () => {
    window.localStorage.setItem(NAME_KEY, "Marta");
    await render();
    const input = el.querySelector<HTMLInputElement>("[data-testid=student-name]")!;
    expect(input.value).toBe("Marta");
    expect(el.textContent).toContain("Not Marta?");
    type(input, "Pau");
    await act(async () => { el.querySelector("form")!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })); });
    await flush();
    expect(JSON.parse(String(calls.find((c) => c.url === "/api/sessions")!.init!.body)).student).toBe("Pau");
    expect(window.localStorage.getItem(NAME_KEY)).toBe("Pau");
  });

  it("still works when storage is blocked (private mode)", async () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("SecurityError"); });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("SecurityError"); });
    const onStart = await render();
    type(el.querySelector<HTMLInputElement>("[data-testid=student-name]")!, "Marta");
    await act(async () => { el.querySelector("form")!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })); });
    await flush();
    expect(onStart).toHaveBeenCalledWith(SESSION);
  });

  it("hands the new session to the normal page once started", async () => {
    await act(async () => root.render(
      <HomeworkGate hwId="hw_1">{(s) => <div data-testid="workspace">{s.student} · {s.homework_id}</div>}</HomeworkGate>,
    ));
    await flush();
    expect(el.querySelector("[data-testid=workspace]")).toBeNull();
    type(el.querySelector<HTMLInputElement>("[data-testid=student-name]")!, "Marta");
    await act(async () => { el.querySelector("form")!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })); });
    await flush();
    expect(el.querySelector("[data-testid=workspace]")!.textContent).toBe("Marta · hw_1");
  });

  it("says so when the link no longer works", async () => {
    await render(vi.fn(), "hw_gone");
    expect(el.querySelector("[role=alert]")!.textContent).toContain("doesn't work any more");
    expect(el.querySelector("[data-testid=student-name]")).toBeNull();
  });
});
