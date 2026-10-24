import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "@fontsource/caveat/400.css";
import "./styles.css";
import App from "./App";
import { createSession } from "./session";
import { demoSession } from "./demo/boot";
import type { Session } from "./protocol";
import { TeacherPage } from "./teacher/TeacherPage";
import { HomeworkGate } from "./teacher/HomeworkStart";

// ?autostart&mode=math&problem=… skips the start screen (demo links, e2e tests);
// ?demo=<stem>[&speed=2] starts a session and replays that fixture recording into the canvas (make demo)
const q = new URLSearchParams(location.search);
const initial: Session | null = q.has("demo")
  ? await demoSession(q.get("demo")!, Number(q.get("speed") ?? 1))
  : q.has("autostart")
  ? await createSession(q.get("mode") ?? "math", q.get("problem") ?? "").catch(() => null)
  : null;

// /teacher: homework, progress and summaries (the server only serves it on the Mac / LAN)
// /?hw=<id>: a homework link → its start screen (title, problem, first name), then the normal page
const teacher = location.pathname.replace(/\/+$/, "") === "/teacher";
const hw = initial ? null : q.get("hw");
const page = teacher ? <TeacherPage />
  : hw ? <HomeworkGate hwId={hw}>{(s) => <App initial={s} />}</HomeworkGate>
  : <App initial={initial} />;
createRoot(document.getElementById("root")!).render(<StrictMode>{page}</StrictMode>);
