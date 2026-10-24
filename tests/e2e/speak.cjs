// Browser test for REQ-020 (speak) with a stubbed SpeechRecognition.
// Usage: node speak.cjs <hub-url> <works|blocked|missing|silent|silent-stop|insecure>
const { chromium } = require("playwright");
const [url, mode] = process.argv.slice(2);

function stub(mode) {
  window.__audioSent = false;
  if (mode === "missing") {
    Object.defineProperty(window, "SpeechRecognition", { value: undefined, configurable: true });
    Object.defineProperty(window, "webkitSpeechRecognition", { value: undefined, configurable: true });
    return;
  }
  class FakeRecognition {
    start() {
      if (mode.startsWith("silent")) return; // a recogniser that never starts and fires nothing
      setTimeout(() => {
        this.onaudiostart && this.onaudiostart();
        if (mode === "blocked") { this.onerror({ error: "not-allowed" }); this.onend && this.onend(); return; }
        this.onresult({ results: [[{ transcript: "solve two x plus three" }]] });
        setTimeout(() => { this.onresult({ results: [[{ transcript: "solve two x plus three equals eleven" }]] }); this.onend(); }, 50);
      }, 50);
    }
    stop() { if (!mode.startsWith("silent")) this.onend && this.onend(); }
    abort() {}
  }
  // Chromium has both names; replace both so the real (silent, headless) recogniser never runs.
  Object.defineProperty(window, "SpeechRecognition", { value: FakeRecognition, configurable: true });
  Object.defineProperty(window, "webkitSpeechRecognition", { value: FakeRecognition, configurable: true });
}

(async () => {
  // "insecure": the same hub under a LAN-style name, which browsers treat as plain HTTP (not localhost).
  const browser = await chromium.launch({ args: ["--host-resolver-rules=MAP hub.lan 127.0.0.1"] });
  const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
  await page.addInitScript(stub, mode);
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  let audioPosted = false;
  page.on("request", (r) => { if (r.method() === "POST" && /audio/.test(r.headers()["content-type"] || "")) audioPosted = true; });
  await page.goto(mode === "insecure" ? url.replace("127.0.0.1", "hub.lan") : url);
  await page.fill("#nickname", "Mic" + mode + (Date.now() % 1000));
  await page.click(".ot-join-form button[type=submit]");
  await page.click(".ot-subject.maths");
  const visible = await page.isVisible("#speak");
  await page.click("#speak");
  await page.waitForTimeout(400);
  const listeningSoon = await page.evaluate(() => document.getElementById("speak").classList.contains("listening"));
  if (mode === "silent-stop") { await page.click("#speak"); await page.waitForTimeout(100); }
  if (mode === "silent") await page.waitForTimeout(8000);
  const notices = await page.$$eval(".ot-notice p", (ps) => ps.map((p) => p.textContent));
  const result = {
    visible,
    listeningSoon,
    secure: await page.evaluate(() => window.isSecureContext),
    text: await page.inputValue("#text"),
    notices,
    focused: await page.evaluate(() => document.activeElement && document.activeElement.id),
    listeningAfter: await page.evaluate(() => document.getElementById("speak").classList.contains("listening")),
    audioPosted,
    errors,
  };
  console.log(JSON.stringify(result));
  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });
