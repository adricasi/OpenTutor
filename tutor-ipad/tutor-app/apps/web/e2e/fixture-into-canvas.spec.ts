// Phase 2 acceptance: draw a recorded fixture into the canvas with pen pointer events;
// the server must hold equivalent strokes (same count, every point within 1 px).
import { expect, test } from "@playwright/test";
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";

const DIR = join(import.meta.dirname, "../../../../ink-tutor/fixtures/recordings");
type P = [number, number, number, number, number, number];
const fixtures = readdirSync(DIR).filter((f) => f.endsWith(".json"))
  .map((f) => ({ name: f, rec: JSON.parse(readFileSync(join(DIR, f), "utf8")) }))
  .filter((f) => f.rec.pages[0].strokes.length > 0);

for (const { name, rec } of fixtures) {
  test(`fixture ${name} drawn with the pen reaches the server intact`, async ({ page, request }) => {
    const strokes: { pts: P[] }[] = rec.pages[0].strokes;
    await page.goto("/?autostart&mode=math");
    const canvas = page.getByTestId("ink");
    await expect(canvas).toBeVisible();
    const box = (await canvas.boundingBox())!;
    expect(box.width).toBeGreaterThanOrEqual(rec.pages[0].width);
    expect(box.height).toBeGreaterThanOrEqual(rec.pages[0].height - 80); // tall strokes still fit

    await page.evaluate((strokes: { pts: P[] }[]) => {
      const c = document.querySelector("canvas.ink") as HTMLCanvasElement;
      const r = c.getBoundingClientRect();
      const ev = (type: string, p: P) => new PointerEvent(type, {
        pointerId: 1, pointerType: "pen", isPrimary: true, bubbles: true, cancelable: true,
        clientX: r.left + p[1], clientY: r.top + p[2], pressure: p[3], tiltX: p[4], tiltY: p[5],
      });
      for (const s of strokes) {
        c.dispatchEvent(ev("pointerdown", s.pts[0]!));
        for (const p of s.pts.slice(1)) c.dispatchEvent(ev("pointermove", p));
        c.dispatchEvent(ev("pointerup", s.pts[s.pts.length - 1]!));
      }
    }, strokes);

    const sid = await page.evaluate(() => (window as unknown as { inkTutor: { session: { id: string } } }).inkTutor.session.id);
    const pageId = await page.evaluate(() => (window as unknown as { inkTutor: { surface: { page: { id: string } } } }).inkTutor.surface.page.id);
    // the pen-up debounce (700 ms) sends the batch; poll the server until it has everything
    let got: { pts: P[] }[] = [];
    await expect.poll(async () => {
      got = await (await request.get(`/api/sessions/${sid}/pages/${pageId}/strokes`)).json();
      return got.length;
    }, { timeout: 10_000 }).toBe(strokes.length);

    for (let i = 0; i < strokes.length; i++) {
      const want = strokes[i]!.pts, have = got[i]!.pts;
      expect(have.length, `stroke ${i} point count`).toBe(want.length);
      for (let j = 0; j < want.length; j++) {
        expect(Math.abs(have[j]![1] - want[j]![1])).toBeLessThanOrEqual(1);
        expect(Math.abs(have[j]![2] - want[j]![2])).toBeLessThanOrEqual(1);
        expect(have[j]![3]).toBeCloseTo(want[j]![3], 2); // pressure preserved
      }
    }
  });
}
