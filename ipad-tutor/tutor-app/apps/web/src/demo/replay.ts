// Play a demo timeline into the ink canvas as synthetic pen pointer events, with the recorded timing.
// The surface handles them like a real Pencil, so the dirty tracker batches and sends ink exactly as
// it would for a hand, and the tutor reacts live.
import type { Step } from "./timeline";

export interface Target {
  pointer(step: Extract<Step, { kind: "down" | "move" | "up" }>): void;
  newPage(): void;
}

export interface Player {
  stop(): void;
  done: Promise<void>;
}

export interface PlayOptions {
  speed?: number;
  leadInMs?: number; // the recording usually starts with a pause: skip all but this much of it
  now?: () => number;
  onProgress?: (t: number) => void; // recording time of the step just played
}

export function play(steps: Step[], target: Target, o: PlayOptions = {}): Player {
  const speed = o.speed && o.speed > 0 ? o.speed : 1;
  const now = o.now ?? (() => performance.now());
  const t0 = Math.max(0, (steps[0]?.t ?? 0) - (o.leadInMs ?? 1000));
  const start = now();
  let i = 0;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let finish: () => void = () => {};
  const done = new Promise<void>((r) => (finish = r));

  const tick = () => {
    const elapsed = (now() - start) * speed;
    while (i < steps.length && steps[i]!.t - t0 <= elapsed) {
      const s = steps[i++]!;
      if (s.kind === "page") target.newPage();
      else target.pointer(s);
      o.onProgress?.(s.t);
    }
    if (i >= steps.length) { timer = null; finish(); return; }
    timer = setTimeout(tick, Math.max(0, (steps[i]!.t - t0 - elapsed) / speed));
  };
  timer = setTimeout(tick, 0);
  return {
    stop: () => { if (timer) clearTimeout(timer); timer = null; i = steps.length; finish(); },
    done,
  };
}

const TYPES = { down: "pointerdown", move: "pointermove", up: "pointerup" } as const;

/** A target that dispatches pen events on the canvas; `setEraser` flips the surface's tool. */
export function canvasTarget(canvas: HTMLCanvasElement, setEraser: (on: boolean) => void, newPage: () => void): Target {
  return {
    pointer(s) {
      if (s.kind === "down") setEraser(s.eraser);
      const r = canvas.getBoundingClientRect();
      canvas.dispatchEvent(new PointerEvent(TYPES[s.kind], {
        pointerId: 1, pointerType: "pen", isPrimary: true, bubbles: true, cancelable: true,
        clientX: r.left + s.x, clientY: r.top + s.y, pressure: s.kind === "up" ? 0 : s.p, tiltX: s.tx, tiltY: s.ty,
      }));
    },
    newPage,
  };
}
