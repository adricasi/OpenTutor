// Teacher view: homework links for students, and the teacher's REST calls (/api/teacher/*,
// Mac / LAN only). Types come from the protocol; nothing here talks to the WebSocket.
import type {
  FeedbackEvent, Homework, HomeworkCreate, HomeworkProgress, HomeworkPublic, LearningLog, PageRef,
  ProgressSummary, Reading, Session, SessionBrief, SessionCreate, Stroke,
} from "../protocol";

export const NAME_KEY = "inktutor.student.v1";

// Storage can throw (private mode, blocked site data): the name then just isn't remembered on this device.
export function loadName(): string {
  try { return window.localStorage.getItem(NAME_KEY) ?? ""; } catch { return ""; }
}

export function saveName(name: string): void {
  try { window.localStorage.setItem(NAME_KEY, name); } catch { /* not remembered */ }
}

async function json<T>(r: Response): Promise<T> {
  if (!r.ok) throw new Error(r.status === 403 ? "The teacher view only opens on the Mac (or its Wi-Fi)." : `HTTP ${r.status}`);
  return (await r.json()) as T;
}

const post = (url: string, body: unknown, method = "POST") =>
  fetch(url, { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

// ---- students
export const getHomework = async (id: string) => json<HomeworkPublic>(await fetch(`/api/homework/${encodeURIComponent(id)}`));

export async function startHomework(hw: HomeworkPublic, student: string): Promise<Session> {
  const body: SessionCreate = { mode: hw.mode, problem: hw.problem, student, homework_id: hw.id };
  return json<Session>(await post("/api/sessions", body));
}

// ---- teacher
export interface HomeworkItem { homework: Homework; students: number; done: number }
export interface SessionPage {
  page: PageRef;
  strokes: Stroke[];
  erased: string[];
  reading: Reading | null;
  feedback: FeedbackEvent[];
}
export interface SessionDetail {
  session: Session;
  homework: Homework | null;
  log: LearningLog;
  summary: ProgressSummary;
  pages: SessionPage[];
}

export const listHomework = async () => json<HomeworkItem[]>(await fetch("/api/teacher/homework"));
export const createHomework = async (b: HomeworkCreate) => json<Homework>(await post("/api/teacher/homework", b));
export const homeworkProgress = async (id: string) => json<HomeworkProgress>(await fetch(`/api/teacher/homework/${id}`));
export const looseSessions = async () => json<SessionBrief[]>(await fetch("/api/teacher/sessions"));
export const sessionDetail = async (id: string) => json<SessionDetail>(await fetch(`/api/teacher/sessions/${id}`));
export async function deleteHomework(id: string): Promise<void> {
  const r = await fetch(`/api/teacher/homework/${id}`, { method: "DELETE" });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
}
export async function refreshSummary(sessionId: string, cloud = false): Promise<{ summary: ProgressSummary; queued: boolean }> {
  return json(await post(`/api/teacher/sessions/${sessionId}/summary${cloud ? "?cloud=true" : ""}`, {}));
}
export const csvUrl = (homework: string) => `/api/teacher/export.csv?homework=${encodeURIComponent(homework)}`;

export function minutes(s: number | undefined): string {
  const m = (s ?? 0) / 60;
  return m < 1 ? (s ? "< 1 min" : "–") : `${Math.round(m)} min`;
}

export const STATUS_LABEL: Record<string, string> = { done: "✓ done", in_progress: "in progress", not_started: "not started" };
export const MODE_LABEL: Record<string, string> = {
  math: "Maths", consulting: "Consulting case", system_design: "System design", diagram: "Diagram",
  language: "Language", accounting: "Accounting", sudoku: "Sudoku", other: "Other",
};
