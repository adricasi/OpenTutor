import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "@fontsource/caveat/400.css";
import "./styles.css";
import App from "./App";
import { createSession } from "./session";
import { demoSession } from "./demo/boot";
import type { Session } from "./protocol";

// ?autostart&mode=math&problem=… skips the start screen (demo links, e2e tests);
// ?demo=<stem>[&speed=2] starts a session and replays that fixture recording into the canvas (make demo)
const q = new URLSearchParams(location.search);
const initial: Session | null = q.has("demo")
  ? await demoSession(q.get("demo")!, Number(q.get("speed") ?? 1))
  : q.has("autostart")
  ? await createSession(q.get("mode") ?? "math", q.get("problem") ?? "").catch(() => null)
  : null;

createRoot(document.getElementById("root")!).render(<StrictMode><App initial={initial} /></StrictMode>);
