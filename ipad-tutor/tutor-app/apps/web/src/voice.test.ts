import { describe, expect, it } from "vitest";
import { BargeIn, toPcm16 } from "./voice/voice";

const tone = (n: number, amp: number) => Float32Array.from({ length: n }, (_, i) => amp * Math.sin(i / 3));

describe("voice", () => {
  it("downsamples 48 kHz to 16 kHz PCM16", () => {
    const pcm = toPcm16(tone(4800, 0.5), 48000);
    expect(pcm.length).toBe(1600);
    expect(Math.max(...pcm)).toBeLessThanOrEqual(32767);
  });

  it("barge-in fires on the 3rd loud 20 ms frame (≈ 60 ms), not on background noise", () => {
    const b = new BargeIn();
    for (let i = 0; i < 50; i++) expect(b.frame(tone(960, 0.005))).toBe(false); // room noise
    expect(b.frame(tone(960, 0.3))).toBe(false);
    expect(b.frame(tone(960, 0.3))).toBe(false);
    expect(b.frame(tone(960, 0.3))).toBe(true); // 60 ms after the user starts talking: stop playback
    expect(b.frame(tone(960, 0.3))).toBe(false); // fires once per utterance
  });
});
