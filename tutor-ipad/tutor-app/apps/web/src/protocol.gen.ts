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
export type ServerMessage =
  SessionMsg | ReadingMsg | FeedbackMsg | StatusMsg | VoiceTranscript | VoiceAudioOut | ErrorMsg | RuntimeMsg;

export interface InkTutorProtocol {
  Anchor?: Anchor;
  BBox?: BBox;
  CheckResult?: CheckResult;
  ClientMessage?: ClientMessage;
  ErrorMsg?: ErrorMsg;
  FeedbackEvent?: FeedbackEvent;
  FeedbackMsg?: FeedbackMsg;
  GhostAction?: GhostAction;
  Graph?: Graph;
  GraphEdge?: GraphEdge;
  GraphNode?: GraphNode;
  HintRequest?: HintRequest;
  InkUpdate?: InkUpdate;
  LineCorrection?: LineCorrection;
  MarkDismiss?: MarkDismiss;
  PageRef?: PageRef;
  QuestionAnswer?: QuestionAnswer;
  Reading?: Reading;
  ReadingLine?: ReadingLine;
  ReadingMsg?: ReadingMsg;
  RuntimeMsg?: RuntimeMsg;
  RuntimeStatus?: RuntimeStatus;
  ServerMessage?: ServerMessage;
  Session?: Session;
  SessionMsg?: SessionMsg;
  SessionUpdate?: SessionUpdate;
  StatusMsg?: StatusMsg;
  Stroke?: Stroke;
  SymbolSpan?: SymbolSpan;
  TutorInfo?: TutorInfo;
  VoiceAudio?: VoiceAudio;
  VoiceAudioOut?: VoiceAudioOut;
  VoiceDictate?: VoiceDictate;
  VoiceStop?: VoiceStop;
  VoiceTranscript?: VoiceTranscript;
}
export interface Anchor {
  line_id?: string | null;
  stroke_ids?: string[] | null;
  bbox?: BBox | null;
  node_id?: string | null;
}
export interface BBox {
  x: number;
  y: number;
  w: number;
  h: number;
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
  mode?: ("math" | "diagram" | "system_design" | "consulting" | "language" | "accounting" | "sudoku" | "other") | null;
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
export interface SessionMsg {
  type?: "session";
  session: Session;
  tier2: TutorInfo;
}
export interface Session {
  id: string;
  mode: "math" | "diagram" | "system_design" | "consulting" | "language" | "accounting" | "sudoku" | "other";
  problem?: string;
  rubric?: string | null;
  reference_answer?: string | null;
  provider?: "local" | "anthropic" | "openai";
  voice?: boolean;
  pages?: PageRef[];
  keep_history?: boolean;
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
