/* Generated from packages/protocol/schema.json by json-schema-to-typescript. Do not edit; run make protocol. */

export type ClientMessage =
  | InkUpdate
  | HintRequest
  | QuestionAnswer
  | GhostAction
  | MarkDismiss
  | SessionUpdate
  | VoiceAudio
  | VoiceStop
  | LineCorrection
  | VoiceDictate;
export type ScreenClientMessage =
  | ScreenHello
  | ScreenFrame
  | AXSnapshot
  | InputEvent
  | GuideGoal
  | GuideControl
  | SessionUpdate
  | VoiceAudio
  | VoiceStop;
export type ScreenServerMessage =
  | OverlayMsg
  | ScreenConfigMsg
  | GuidePlanMsg
  | GuideStatusMsg
  | VoiceTranscript
  | VoiceAudioOut
  | RuntimeMsg
  | ErrorMsg;
export type ServerMessage =
  SessionMsg | ReadingMsg | FeedbackMsg | StatusMsg | VoiceTranscript | VoiceAudioOut | ErrorMsg | RuntimeMsg;

export interface InkTutorProtocol {
  AXElement?: AXElement;
  AXSnapshot?: AXSnapshot;
  Anchor?: Anchor;
  BBox?: BBox;
  CheckResult?: CheckResult;
  ClientMessage?: ClientMessage;
  DisplayInfo?: DisplayInfo;
  DoneWhen?: DoneWhen;
  ErrorMsg?: ErrorMsg;
  FeedbackEvent?: FeedbackEvent;
  FeedbackMsg?: FeedbackMsg;
  GhostAction?: GhostAction;
  Graph?: Graph;
  GraphEdge?: GraphEdge;
  GraphNode?: GraphNode;
  GuideControl?: GuideControl;
  GuideGoal?: GuideGoal;
  GuidePlan?: GuidePlan;
  GuidePlanMsg?: GuidePlanMsg;
  GuideStatusMsg?: GuideStatusMsg;
  GuideStep?: GuideStep;
  HintRequest?: HintRequest;
  Homework?: Homework;
  HomeworkCreate?: HomeworkCreate;
  HomeworkProgress?: HomeworkProgress;
  HomeworkPublic?: HomeworkPublic;
  InkUpdate?: InkUpdate;
  InputEvent?: InputEvent;
  LearningLog?: LearningLog;
  LineCorrection?: LineCorrection;
  LogLine?: LogLine;
  MarkDismiss?: MarkDismiss;
  OverlayEvent?: OverlayEvent;
  OverlayMsg?: OverlayMsg;
  PageRef?: PageRef;
  Point2?: Point2;
  ProgressSummary?: ProgressSummary;
  QuestionAnswer?: QuestionAnswer;
  Reading?: Reading;
  ReadingLine?: ReadingLine;
  ReadingMsg?: ReadingMsg;
  RuntimeMsg?: RuntimeMsg;
  RuntimeStatus?: RuntimeStatus;
  ScreenClientMessage?: ScreenClientMessage;
  ScreenConfigMsg?: ScreenConfigMsg;
  ScreenFrame?: ScreenFrame;
  ScreenHello?: ScreenHello;
  ScreenServerMessage?: ScreenServerMessage;
  ServerMessage?: ServerMessage;
  Session?: Session;
  SessionBrief?: SessionBrief;
  SessionCreate?: SessionCreate;
  SessionMsg?: SessionMsg;
  SessionUpdate?: SessionUpdate;
  StatusMsg?: StatusMsg;
  Stroke?: Stroke;
  StudentRow?: StudentRow;
  SymbolSpan?: SymbolSpan;
  Target?: Target;
  TutorInfo?: TutorInfo;
  VerdictCount?: VerdictCount;
  VoiceAudio?: VoiceAudio;
  VoiceAudioOut?: VoiceAudioOut;
  VoiceDictate?: VoiceDictate;
  VoiceStop?: VoiceStop;
  VoiceTranscript?: VoiceTranscript;
}
/**
 * One accessibility element of the frontmost app (role/title/frame); its value is never sent for secure fields.
 */
export interface AXElement {
  id: string;
  role: string;
  title?: string | null;
  value?: string | null;
  frame: BBox;
  enabled?: boolean;
  path?: string[];
}
export interface BBox {
  x: number;
  y: number;
  w: number;
  h: number;
}
/**
 * The frontmost app's visible elements. Empty (and secure_field_focused) while a password field has focus.
 */
export interface AXSnapshot {
  type?: "ax.snapshot";
  app: string;
  bundle_id?: string;
  window_title?: string;
  display?: number;
  elements?: AXElement[];
  focused_id?: string | null;
  secure_field_focused?: boolean;
  pointer?: Point2 | null;
  t_ms: number;
}
export interface Point2 {
  x: number;
  y: number;
}
export interface Anchor {
  line_id?: string | null;
  stroke_ids?: string[] | null;
  bbox?: BBox | null;
  node_id?: string | null;
}
export interface CheckResult {
  ok: boolean;
  kind: string;
  anchor: Anchor;
  detail: string;
  expected?: string | null;
}
export interface InkUpdate {
  type?: "ink.update";
  page_id: string;
  strokes_changed?: Stroke[];
  strokes_removed?: string[];
  page?: PageRef | null;
}
export interface Stroke {
  id: string;
  page_id: string;
  color?: string;
  width?: number;
  pointer_type?: "pen" | "mouse" | "touch";
  pts: [number, number, number, number, number, number][];
}
export interface PageRef {
  id: string;
  index: number;
  width: number;
  height: number;
}
export interface HintRequest {
  type?: "hint.request";
  page_id: string;
  feedback_id?: string | null;
  line_id?: string | null;
  level: number;
  explicit?: boolean;
}
export interface QuestionAnswer {
  type?: "question.answer";
  page_id: string;
  feedback_id: string;
  reading_line_id: string;
  chosen: string;
}
export interface GhostAction {
  type: "ghost.accept" | "ghost.dismiss";
  page_id: string;
  feedback_id: string;
}
export interface MarkDismiss {
  type?: "mark.dismiss";
  page_id: string;
  feedback_id: string;
}
export interface SessionUpdate {
  type?: "session.update";
  provider?: ("local" | "anthropic" | "openai") | null;
  voice?: boolean | null;
  problem?: string | null;
  mode?:
    | ("math" | "diagram" | "system_design" | "consulting" | "language" | "accounting" | "sudoku" | "screen" | "other")
    | null;
  keep_history?: boolean | null;
}
export interface VoiceAudio {
  type?: "voice.audio";
  pcm16_b64: string;
  sample_rate?: number;
  pen?: Anchor | null;
}
export interface VoiceStop {
  type?: "voice.stop";
}
/**
 * "Tell the tutor what you meant": what a line really says, typed (or dictated) by the student from a question
 * chip or a "?" mark. Only the reading changes; the ink is never touched.
 */
export interface LineCorrection {
  type?: "line.correction";
  page_id: string;
  line_id: string;
  text: string;
  source?: "typed" | "spoken";
}
/**
 * The next utterance fills the correction field for this line (it is not a question for the tutor).
 */
export interface VoiceDictate {
  type?: "voice.dictate";
  page_id: string;
  line_id: string;
  cancel?: boolean;
}
export interface DisplayInfo {
  id: number;
  frame: BBox;
  scale?: number;
  main?: boolean;
}
/**
 * How the stepper knows a step is done: state predicates first; the user's "next" always works.
 */
export interface DoneWhen {
  kind: "ax_exists" | "ax_value" | "window_title" | "vlm_yes_no" | "user_next";
  args?: {
    [k: string]: string;
  };
}
export interface ErrorMsg {
  type?: "error";
  message: string;
}
export interface FeedbackEvent {
  id: string;
  page_id: string;
  anchor: Anchor;
  type: "mark" | "ghost" | "hint" | "question" | "speak" | "explain" | "summary" | "retract";
  glyph?: ("check" | "cross" | "question" | "pin" | "underline" | "circle") | null;
  severity?: ("ok" | "minor" | "major") | null;
  text?: string | null;
  hint_level?: number | null;
  refers_to?: string | null;
  source?: "checker" | "tutor" | "recognizer";
  choices?: string[];
}
export interface FeedbackMsg {
  type?: "feedback";
  feedback: FeedbackEvent;
  provider?: string;
  model?: string;
  ms?: number;
}
export interface Graph {
  nodes?: GraphNode[];
  edges?: GraphEdge[];
}
export interface GraphNode {
  id: string;
  label: string;
  bbox: BBox;
  stroke_ids: string[];
  shape?: string | null;
}
export interface GraphEdge {
  id: string;
  src: string;
  dst: string;
  label?: string | null;
  directed?: boolean;
  stroke_ids?: string[];
}
export interface GuideControl {
  type?: "guide.control";
  action: "next" | "back" | "repeat" | "pause" | "resume" | "stop";
}
export interface GuideGoal {
  type?: "guide.goal";
  text: string;
}
export interface GuidePlan {
  goal: string;
  app: string;
  steps: GuideStep[];
  version?: number;
}
export interface GuideStep {
  id: string;
  n: number;
  instruction: string;
  speak: string;
  target?: Target | null;
  done_when: DoneWhen;
  hint_levels?: string[];
}
export interface Target {
  ax_path?: string[] | null;
  title_regex?: string | null;
  role?: string | null;
  description?: string | null;
  bbox?: BBox | null;
  source?: ("ax" | "vlm") | null;
}
export interface GuidePlanMsg {
  type?: "guide.plan";
  plan: GuidePlan;
}
/**
 * What the tutor is doing, for the phantom icon and its transcript ("Got it — planning…", "Step 2 of 5").
 */
export interface GuideStatusMsg {
  type?: "guide.status";
  phase: "idle" | "planning" | "locating" | "guiding" | "verifying" | "replanning" | "paused" | "done" | "error";
  step?: number;
  of?: number;
  detail?: string;
}
export interface Homework {
  title: string;
  mode?:
    "math" | "diagram" | "system_design" | "consulting" | "language" | "accounting" | "sudoku" | "screen" | "other";
  problem?: string;
  due?: string | null;
  answer?: string | null;
  id: string;
  created: number;
}
export interface HomeworkCreate {
  title: string;
  mode?:
    "math" | "diagram" | "system_design" | "consulting" | "language" | "accounting" | "sudoku" | "screen" | "other";
  problem?: string;
  due?: string | null;
  answer?: string | null;
}
export interface HomeworkProgress {
  homework: Homework;
  link: string;
  qr: string;
  rows?: StudentRow[];
}
export interface StudentRow {
  student: string;
  status: "done" | "in_progress" | "not_started";
  session_id: string;
  sessions?: string[];
  time_on_task_s?: number;
  hints?: number;
  hints_explicit?: number;
  mistakes?: number;
  mistakes_fixed?: number;
  last_activity?: number;
  summary?: ProgressSummary | null;
}
export interface ProgressSummary {
  session_id: string;
  text: string;
  source: string;
  log_hash: string;
  created: number;
  stale?: boolean;
}
/**
 * What a student's device sees for a homework link: never who else did it.
 */
export interface HomeworkPublic {
  id: string;
  title: string;
  mode: "math" | "diagram" | "system_design" | "consulting" | "language" | "accounting" | "sudoku" | "screen" | "other";
  problem: string;
  due?: string | null;
}
/**
 * That the user clicked or typed, and where they clicked. Keys carry no content, not even key codes.
 */
export interface InputEvent {
  type?: "input.event";
  kind: "click" | "key";
  t_ms: number;
  at?: Point2 | null;
}
/**
 * Derived, deterministically, from what the store already holds (progress.py).
 */
export interface LearningLog {
  session_id: string;
  mode: "math" | "diagram" | "system_design" | "consulting" | "language" | "accounting" | "sudoku" | "screen" | "other";
  problem?: string;
  student?: string | null;
  homework_id?: string | null;
  created?: number;
  last_activity?: number;
  lines_written?: number;
  lines_checked?: number;
  verdicts?: VerdictCount[];
  mistakes?: number;
  mistakes_fixed?: number;
  ghosts_retired?: number;
  hints?: number[];
  questions_asked?: number;
  questions_answered?: number;
  corrections?: number;
  tutor_marks?: number;
  strokes?: number;
  time_on_task_s?: number;
  finished?: boolean | null;
  finished_by?: "answer" | "last_line" | "";
  lines?: LogLine[];
}
export interface VerdictCount {
  kind: string;
  ok?: number;
  wrong?: number;
}
/**
 * One checked line in the learning log (the reading is short text; no strokes, no images).
 */
export interface LogLine {
  line_id: string;
  page_id: string;
  kind: string;
  reads?: string;
  ok?: boolean | null;
  was_wrong?: boolean;
  fixed?: boolean;
  label?: string;
  detail?: string;
  hint_level?: number;
}
/**
 * One thing the helper draws over the screen (or clears). It points; nothing here can act on the screen.
 */
export interface OverlayEvent {
  id: string;
  type: "highlight" | "arrow" | "caption" | "pulse" | "clear" | "phantom_state";
  bbox?: BBox | null;
  from_point?: Point2 | null;
  text?: string | null;
  step_id?: string | null;
  ttl_ms?: number | null;
  refers_to?: string | null;
  state?: ("idle" | "listening" | "thinking" | "speaking" | "paused" | "capture_paused") | null;
}
export interface OverlayMsg {
  type?: "overlay";
  event: OverlayEvent;
  provider?: string;
  model?: string;
}
export interface Reading {
  page_id: string;
  lines?: ReadingLine[];
  graph?: Graph | null;
  version?: number;
}
export interface ReadingLine {
  id: string;
  stroke_ids: string[];
  bbox: BBox;
  kind: "math" | "text" | "label" | "long_division";
  latex?: string | null;
  text?: string | null;
  confidence: number;
  alternatives?: string[];
  symbol_map?: SymbolSpan[];
  structure?: {
    [k: string]: unknown;
  } | null;
  corrected?: ("typed" | "spoken") | null;
}
export interface SymbolSpan {
  symbol: string;
  index: number;
  stroke_ids: string[];
}
export interface ReadingMsg {
  type?: "reading";
  reading: Reading;
  ms?: number;
}
export interface RuntimeMsg {
  type?: "runtime";
  status: RuntimeStatus;
}
export interface RuntimeStatus {
  available_gb: number;
  level: "ok" | "low" | "critical";
  tier2_model: string;
  tier2_mode: "full" | "offload" | "refused" | "external" | "loading" | "unavailable" | "off";
  detail?: string;
  event?: string;
  event_id?: number;
}
/**
 * Sent on connect and whenever the display layout changes, so the server can keep boxes on screen.
 */
export interface ScreenHello {
  type?: "screen.hello";
  helper?: string;
  displays?: DisplayInfo[];
}
/**
 * A downscaled screenshot of one display. Never leaves the Mac (screen mode is local-only).
 */
export interface ScreenFrame {
  type?: "screen.frame";
  id: string;
  display: number;
  origin: Point2;
  width: number;
  height: number;
  scale: number;
  jpeg_b64: string;
  window?: BBox | null;
  t_ms: number;
  trigger: "tick" | "click" | "focus";
}
/**
 * Sent on connect. The helper skips blocklisted apps' windows before capture (Phase 12).
 */
export interface ScreenConfigMsg {
  type?: "screen.config";
  blocklist: string[];
  capture_fps?: number;
  allow_automation?: boolean;
}
export interface VoiceTranscript {
  type?: "voice.transcript";
  text: string;
  final: boolean;
}
export interface VoiceAudioOut {
  type?: "voice.audio";
  pcm16_b64: string;
  sample_rate: number;
  feedback_id?: string | null;
}
export interface SessionMsg {
  type?: "session";
  session: Session;
  tier2: TutorInfo;
}
export interface Session {
  id: string;
  mode: "math" | "diagram" | "system_design" | "consulting" | "language" | "accounting" | "sudoku" | "screen" | "other";
  problem?: string;
  rubric?: string | null;
  reference_answer?: string | null;
  provider?: "local" | "anthropic" | "openai";
  voice?: boolean;
  pages?: PageRef[];
  keep_history?: boolean;
  student?: string | null;
  homework_id?: string | null;
}
export interface TutorInfo {
  provider: "local" | "anthropic" | "openai";
  model: string;
  ready: boolean;
  detail?: string;
}
export interface StatusMsg {
  type?: "status";
  page_id?: string | null;
  phase: "received" | "recognizing" | "checking" | "reviewing" | "waiting" | "idle" | "cancelled" | "error";
  detail?: string;
  line_ids?: string[];
}
export interface SessionBrief {
  session_id: string;
  mode: "math" | "diagram" | "system_design" | "consulting" | "language" | "accounting" | "sudoku" | "screen" | "other";
  problem?: string;
  student?: string | null;
  created?: number;
  log: LearningLog;
  summary?: ProgressSummary | null;
}
/**
 * POST /api/sessions. `student` / `homework_id` come from a homework link (`/?hw=<id>`).
 */
export interface SessionCreate {
  mode?:
    "math" | "diagram" | "system_design" | "consulting" | "language" | "accounting" | "sudoku" | "screen" | "other";
  problem?: string;
  provider?: ("local" | "anthropic" | "openai") | null;
  student?: string | null;
  homework_id?: string | null;
}
