// Browser test for REQ-018 (read aloud). Usage: node read_aloud.cjs <hub-url> <local|none>
// Stubs speechSynthesis so the result doesn't depend on the machine's installed voices.
const { chromium } = require("playwright");

const [url, mode] = process.argv.slice(2);

function stub(mode) {
  const voices = mode === "local"
    ? [
        { name: "Cloud English", lang: "en-US", localService: false },
        { name: "Phone English", lang: "en-GB", localService: true },
      ]
    : [{ name: "Cloud English", lang: "en-US", localService: false }];
  window.__spoken = [];
  window.__cancelled = 0;
  window.SpeechSynthesisUtterance = function (text) { this.text = text; };
  // speechSynthesis is a getter on Window, so plain assignment is ignored.
  Object.defineProperty(window, "speechSynthesis", { configurable: true, value: {
    getVoices: () => voices,
    addEventListener: () => {},
    cancel: () => { window.__cancelled += 1; },
    speak: (u) => { window.__spoken.push({ text: u.text, voice: u.voice.name }); },
  } });
}

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 360, height: 740 } });
  await page.addInitScript(stub, mode);
  const posts = [];
  page.on("request", (r) => { if (r.method() === "POST") posts.push(r.url()); });
  await page.goto(url);
  await page.fill("#nickname", `Ana-${mode}`);
  await page.click("button[type=submit]");
  await page.click(".ot-subject.maths");
  await page.fill("#text", "Solve 2x + 3 = 11");
  await page.click("#ask button[type=submit]");
  await page.waitForSelector(".ot-turn.tutor .ot-turn-text");
  await page.waitForFunction(() => document.querySelectorAll(".speak").length >= 1);
  const visible = await page.$$eval(".speak", (bs) => bs.filter((b) => !b.hidden).length);
  const result = { visible, spoken: [], labels: [], postsDuringSpeech: 0 };
  if (mode === "local") {
    const before = posts.length;
    await page.click(".speak >> nth=0");
    result.labels.push((await page.textContent(".speak >> nth=0")).trim());
    await page.click(".speak >> nth=0");
    result.labels.push((await page.textContent(".speak >> nth=0")).trim());
    result.spoken = await page.evaluate(() => window.__spoken);
    result.postsDuringSpeech = posts.length - before;
  }
  console.log(JSON.stringify(result));
  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });
