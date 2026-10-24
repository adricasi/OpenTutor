// The Talk button on iPad sizes, and the real audio path with Chromium's fake microphone.
// The e2e server runs without models (no runtime): it answers `session.update {voice: true}` with voice still off.
// The "audio path" test therefore stands in for the voice server on the WebSocket (page.routeWebSocket): every other
// message goes to the real server, `session.update {voice}` is answered like a server whose models loaded.
import { expect, test, type Page } from "@playwright/test";

test.use({
  permissions: ["microphone"],
  launchOptions: { args: ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream"] },
});

type Msg = { type?: string; [k: string]: unknown };
const parse = (m: string | Buffer): Msg => { try { return JSON.parse(String(m)) as Msg; } catch { return {}; } };

/** Record what the page sends; `fakeVoice` answers voice on/off itself instead of the (model-less) server. */
async function tapSocket(page: Page, fakeVoice: boolean) {
  const sent: Msg[] = [];
  const toPage: { send: (m: string) => void } = { send: () => {} };
  await page.routeWebSocket(/\/ws\/session\//, (ws) => {
    const server = ws.connectToServer();
    toPage.send = (m) => ws.send(m);
    ws.onMessage((raw) => {
      const msg = parse(raw);
      sent.push(msg);
      if (fakeVoice && msg.type === "session.update" && typeof msg.voice === "boolean") {
        if (msg.voice) {
          ws.send(JSON.stringify({ type: "status", phase: "recognizing", detail: "loading voice…" }));
          setTimeout(() => ws.send(JSON.stringify({ type: "status", phase: "waiting", detail: "voice on" })), 1000);
        }
        return;
      }
      server.send(raw);
    });
    server.onMessage((raw) => ws.send(raw));
  });
  return { sent, toPage };
}

/** ~`secs` of a 440 Hz tone as PCM16 base64 at 24 kHz: what the server's TTS sends. */
function tutorClip(secs: number): string {
  const n = Math.round(24000 * secs);
  const pcm = new Int16Array(n);
  for (let i = 0; i < n; i++) pcm[i] = Math.round(8000 * Math.sin((2 * Math.PI * 440 * i) / 24000));
  return Buffer.from(pcm.buffer).toString("base64");
}

const SIZES = [
  { name: "iPad portrait 820×1180", width: 820, height: 1180 },
  { name: "iPad landscape 1180×820", width: 1180, height: 820 },
  { name: "iPad Pro portrait 1024×1366", width: 1024, height: 1366 },
  { name: "iPad Pro landscape 1366×1024", width: 1366, height: 1024 },
];

for (const size of SIZES) {
  test(`Talk is visible and clickable at ${size.name}; the toolbar fits`, async ({ page }) => {
    await page.setViewportSize({ width: size.width, height: size.height });
    await page.goto("/?autostart&mode=math");
    await expect(page.getByTestId("ink")).toBeVisible();

    const talk = page.getByTestId("talk");
    await expect(talk).toBeVisible();
    await expect(talk).toBeInViewport({ ratio: 1 });
    await expect(talk).toHaveText(/Talk/);
    await talk.click({ trial: true }); // actionable: visible, stable, enabled, not covered

    // toolbar: nothing pushed off-screen; the compact Talk entry and More stay in view
    await expect(page.getByTestId("voice")).toBeInViewport({ ratio: 1 });
    const bar = await page.locator("header.bar").evaluate((el) => ({ sw: el.scrollWidth, cw: el.clientWidth }));
    expect(bar.sw).toBeLessThanOrEqual(bar.cw);
    const right = (await page.locator("header.bar .right").boundingBox())!;
    expect(right.x + right.width).toBeLessThanOrEqual(size.width);
    const more = page.getByTestId("more");
    await expect(more).toBeInViewport({ ratio: 1 }); // all four sizes are below the 1400 px "everything inline" width
    await more.click();
    const settings = page.getByTestId("more-group").getByRole("button", { name: "Settings" });
    await expect(settings).toBeInViewport({ ratio: 1 });
    await expect(page.getByTestId("more-group").getByRole("button", { name: "Panel" })).toBeInViewport({ ratio: 1 });
  });
}

test("audio path: Talk sends session.update {voice: true} and streams 16 kHz PCM16 from the mic", async ({ page }) => {
  await page.setViewportSize({ width: 820, height: 1180 });
  const { sent, toPage } = await tapSocket(page, true);
  await page.goto("/?autostart&mode=math");
  const talk = page.getByTestId("talk");
  await talk.click();

  await expect.poll(() => sent.some((m) => m.type === "session.update" && m.voice === true)).toBe(true);
  await expect(talk).toHaveText(/Loading voice/); // the ~4 s wait the first time is visible on the button
  await expect(talk).toHaveText(/Listening/);
  await expect(page.getByTestId("voice-bubble")).toContainText("Listening");

  // frames keep coming: ~10 per second, 100 ms of 16 kHz mono PCM16 each (~3.2 kB)
  await expect.poll(() => sent.filter((m) => m.type === "voice.audio").length, { timeout: 10_000 }).toBeGreaterThanOrEqual(10);
  const frames = sent.filter((m) => m.type === "voice.audio");
  for (const f of frames.slice(0, 10)) {
    expect(f.sample_rate).toBe(16000);
    const bytes = Buffer.from(String(f.pcm16_b64), "base64").length;
    expect(bytes % 2).toBe(0);
    expect(bytes).toBeGreaterThan(2800);
    expect(bytes).toBeLessThan(3800);
  }
  // the fake device beeps: the level meter moves
  await expect.poll(async () => Number(await page.getByTestId("level-meter").getAttribute("aria-valuenow")), { timeout: 10_000 })
    .toBeGreaterThan(0);

  // the tutor talks; Stop (or a barge-in on the fake mic's beep, whichever comes first) stops it and tells the server
  toPage.send(JSON.stringify({ type: "voice.audio", pcm16_b64: tutorClip(4), sample_rate: 24000 }));
  await page.getByTestId("voice-stop").click({ timeout: 3000 }).catch(() => { /* barge-in already stopped it */ });
  await expect.poll(() => sent.some((m) => m.type === "voice.stop")).toBe(true);
  await expect(talk).toHaveText(/Listening/);

  // off again
  await talk.click();
  await expect.poll(() => sent.some((m) => m.type === "session.update" && m.voice === false)).toBe(true);
  await expect(talk).toHaveText("Talk");
  const n = sent.filter((m) => m.type === "voice.audio").length;
  await page.waitForTimeout(500);
  expect(sent.filter((m) => m.type === "voice.audio").length).toBeLessThanOrEqual(n + 1); // the mic is off
});

test("server without voice models: the error is shown clearly and Talk returns to off", async ({ page }) => {
  await page.setViewportSize({ width: 1180, height: 820 });
  const { sent } = await tapSocket(page, false); // the real server answers
  await page.goto("/?autostart&mode=math");
  await expect(page.locator(".dot.on")).toBeVisible(); // connected
  const talk = page.getByTestId("talk");
  await talk.click();
  await expect.poll(() => sent.some((m) => m.type === "session.update" && m.voice === true)).toBe(true);

  await expect(talk).toHaveText("Talk", { timeout: 15_000 });
  await expect(talk).toHaveAttribute("aria-pressed", "false");
  const note = page.getByTestId("voice-note");
  await expect(note).toBeVisible();
  await expect(note).toContainText(/without (its )?models|not downloaded|memory|couldn.t/i);
  await expect(page.getByTestId("toast").filter({ hasText: /Voice/ })).toBeVisible();
});
