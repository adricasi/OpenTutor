// `/?demo=<stem>[&speed=2]` (make demo): start a session from a fixture recording and replay it into the
// canvas once the workspace is up. A small pill shows progress and stops the replay.
import "./demo.css";
import { createSession } from "../session";
import type { Session } from "../protocol";
import { canvasTarget, play } from "./replay";
import { timelineEnd, toTimeline, type DemoScript } from "./timeline";

interface Hook { surface?: { eraser: boolean; newPage(): unknown }; session?: Session }

const hook = () => (window as unknown as { inkTutor?: Hook }).inkTutor;
const mmss = (ms: number) => `${Math.floor(ms / 60000)}:${String(Math.floor(ms / 1000) % 60).padStart(2, "0")}`;

export async function fetchDemo(stem: string): Promise<DemoScript> {
  const r = await fetch(`/api/demo/${encodeURIComponent(stem)}`);
  if (!r.ok) throw new Error(`demo ${stem}: HTTP ${r.status}`);
  return (await r.json()) as DemoScript;
}

/** The session the app should open with; the replay starts by itself once the canvas exists. */
export async function demoSession(stem: string, speed = 1): Promise<Session | null> {
  try {
    const script = await fetchDemo(stem);
    const session = await createSession(script.mode, script.problem);
    void runWhenReady(script, session, speed);
    return session;
  } catch (e) {
    console.error(e);
    return null;
  }
}

async function runWhenReady(script: DemoScript, session: Session, speed: number): Promise<void> {
  const ready = () => hook()?.session?.id === session.id && document.querySelector<HTMLCanvasElement>("canvas.ink");
  for (let i = 0; i < 200 && !ready(); i++) await new Promise((r) => setTimeout(r, 100));
  const canvas = ready();
  if (!canvas) return;
  await new Promise((r) => setTimeout(r, 800)); // let the WebSocket connect so the first batch isn't queued

  const steps = toTimeline(script.events);
  const end = timelineEnd(steps);
  const pill = document.createElement("div");
  pill.className = "demo-pill";
  pill.dataset.testid = "demo";
  const label = document.createElement("span");
  const stop = document.createElement("button");
  stop.textContent = "Stop";
  pill.append(label, stop);
  document.body.append(pill);
  const show = (t: number, state = "Demo replay") => {
    label.textContent = `${state} · ${script.stem} · ${mmss(t)} / ${mmss(end)}${speed !== 1 ? ` · ${speed}×` : ""}`;
  };
  show(0);

  const player = play(steps, canvasTarget(
    canvas,
    (on) => { const s = hook()?.surface; if (s) s.eraser = on; },
    () => { hook()?.surface?.newPage(); },
  ), { speed, onProgress: (t) => show(t) });
  stop.onclick = () => player.stop();
  await player.done;
  const s = hook()?.surface;
  if (s) s.eraser = false; // hand the pen back
  show(end, "Demo finished");
  stop.textContent = "Close";
  stop.onclick = () => pill.remove();
}
