import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { PairButton, PairingPage, type PairingInfo } from "./PairingPage";

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const lan = { url: "http://192.168.1.23:8000/", mdns: "http://studio.local:8000/", qr: "data:image/svg+xml,lan", note: "Plain http: Safari blocks the microphone here." };
const noTunnel: PairingInfo = { tunnel: null, lan, bonjour: null, hint: "Run `make pair`" };
const withTunnel: PairingInfo = {
  tunnel: { url: "https://lemon-tiger.trycloudflare.com/?k=T0k", qr: "data:image/svg+xml,tunnel", reachable: true },
  lan, bonjour: "Ink Tutor", hint: null,
};

let root: Root;
let el: HTMLDivElement;
let replies: PairingInfo[];

beforeEach(() => {
  el = document.createElement("div");
  document.body.append(el);
  root = createRoot(el);
  replies = [];
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(replies.length > 1 ? replies.shift() : replies[0]))));
});
afterEach(() => {
  act(() => root.unmount());
  el.remove();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

const flush = () => act(async () => { await new Promise((r) => setTimeout(r, 0)); });

describe("Pair iPad page", () => {
  it("shows the LAN URLs and asks for make pair when there is no tunnel", async () => {
    replies = [noTunnel];
    await act(async () => root.render(<PairingPage onClose={() => {}} />));
    await flush();
    const tunnel = el.querySelector("[data-testid=pairing-tunnel]")!;
    expect(tunnel.textContent).toContain("make pair");
    expect(tunnel.querySelector("img")).toBeNull();
    const lanBox = el.querySelector("[data-testid=pairing-lan]")!;
    expect(lanBox.textContent).toContain("http://192.168.1.23:8000/");
    expect(lanBox.textContent).toContain("http://studio.local:8000/");
    expect(lanBox.textContent).toContain("microphone");
    expect(lanBox.querySelector("img")!.getAttribute("src")).toBe("data:image/svg+xml,lan");
    expect(fetch).toHaveBeenCalledWith("/api/pairing");
  });

  it("picks up the tunnel QR once make pair is running (polling)", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    replies = [noTunnel, withTunnel];
    await act(async () => root.render(<PairingPage onClose={() => {}} pollMs={1000} />));
    await flush();
    expect(el.querySelector("[data-testid=pairing-tunnel] img")).toBeNull();
    await act(async () => { vi.advanceTimersByTime(1000); });
    await flush();
    const img = el.querySelector("[data-testid=pairing-tunnel] img")!;
    expect(img.getAttribute("src")).toBe("data:image/svg+xml,tunnel");
    expect(el.textContent).toContain("https://lemon-tiger.trycloudflare.com/?k=T0k");
    expect(el.textContent).toContain("Bonjour");
  });

  it("says so when the server can't be reached", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("", { status: 502 })));
    await act(async () => root.render(<PairingPage onClose={() => {}} />));
    await flush();
    expect(el.textContent).toContain("Can't reach the server (HTTP 502)");
  });

  it("opens from the start-screen button and closes", async () => {
    replies = [noTunnel];
    await act(async () => root.render(<PairButton />));
    expect(el.querySelector("[role=dialog]")).toBeNull();
    await act(async () => el.querySelector<HTMLButtonElement>(".pair-link")!.click());
    await flush();
    expect(el.querySelector("[role=dialog]")).not.toBeNull();
    await act(async () => el.querySelector<HTMLButtonElement>("[aria-label=Close]")!.click());
    expect(el.querySelector("[role=dialog]")).toBeNull();
  });
});
