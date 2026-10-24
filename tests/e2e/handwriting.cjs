// Browser test for REQ-019 (write by hand). Usage: node handwriting.cjs <hub-url> [screenshot.png]
const { chromium } = require("playwright");
const [url, shot] = process.argv.slice(2);
(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto(url);
  await page.fill("#nickname", "Pen" + (Date.now() % 10000));
  await page.click(".ot-join-form button[type=submit]");
  await page.click(".ot-subject.maths");
  await page.fill("#text", "2x + 3 = 11");
  await page.click("#ask button[type=submit]");
  await page.waitForSelector(".ot-turn.tutor .ot-turn-text");
  await page.click("#ask [data-open-pad]");
  const box = await (await page.$("#pad-canvas")).boundingBox();
  const draw = async (points) => {
    await page.mouse.move(box.x + points[0][0], box.y + points[0][1]);
    await page.mouse.down();
    for (const [x, y] of points.slice(1)) await page.mouse.move(box.x + x, box.y + y, { steps: 4 });
    await page.mouse.up();
  };
  await draw([[30, 40], [60, 90]]); await draw([[60, 40], [30, 90]]);   // x
  await draw([[90, 55], [120, 55]]); await draw([[90, 75], [120, 75]]); // =
  await draw([[140, 40], [170, 40], [150, 95]]);                        // 7
  await draw([[200, 40], [230, 90]]);
  await page.click("#pad-undo");                                         // remove the last stroke
  await page.waitForSelector("#pad-marks .ot-mark", { timeout: 10000 }); // live mark after the pause
  const marks = await page.$$eval("#pad-marks .ot-mark", (els) => els.map((e) => e.className));
  const liveReading = (await page.textContent("#pad-reading")).trim();
  const liveStatus = (await page.textContent("#pad-status")).trim();
  const turnsBeforeUse = await page.locator(".ot-turn").count();
  if (shot) await page.screenshot({ path: shot.replace(/\.png$/, "-live.png") });
  await page.click("#pad-use");
  await page.waitForSelector("#photo-preview:not([hidden])");
  const previewLabel = await page.textContent("#photo-name");
  await page.click("[data-intent=check]");
  await page.waitForSelector(".ot-reading");
  await page.waitForSelector(".ot-verdict");
  const result = {
    previewLabel,
    marks,
    liveReading,
    liveStatus,
    turnsBeforeUse,
    padHiddenAfterUse: await page.isHidden("#pad"),
    reading: await page.textContent(".ot-reading"),
    verdict: (await page.locator(".ot-verdict").last().textContent()).trim(),
    errors,
  };
  if (shot) await page.screenshot({ path: shot, fullPage: true });
  console.log(JSON.stringify(result));
  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });
