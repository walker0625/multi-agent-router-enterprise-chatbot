/* scripts/export_contract.py 의 스키마에서 생성됨 — 직접 고치지 않는다 */

export type StreamEvent =
  | TurnEvent
  | RouteEvent
  | ProgressEvent
  | DeltaEvent
  | ReferencesEvent
  | ChannelDoneEvent
  | HitlEvent
  | ErrorEvent
  | DoneEvent;
export type Outcome = "SINGLE" | "PARALLEL" | "HITL" | "BLOCKED";
export type ChannelStatus = "ok" | "truncated" | "error" | "stopped";
export type ErrorCode =
  | "INVALID_REQUEST"
  | "UNAUTHORIZED"
  | "FORBIDDEN"
  | "NOT_FOUND"
  | "CONFLICT"
  | "PAYLOAD_TOO_LARGE"
  | "TIMEOUT"
  | "CONTENT_FILTERED"
  | "OUTPUT_BLOCKED"
  | "UPSTREAM_ERROR"
  | "AGENT_UNAVAILABLE"
  | "SEARCH_FAILED"
  | "BUDGET_EXCEEDED"
  | "INTERNAL";
export type TurnStatus = "pending" | "done" | "hitl" | "blocked" | "error" | "stopped" | "disconnected";

export interface TurnEvent {
  type: "turn";
  chat_id: string;
  turn_id: string;
  turn_no: number;
}
export interface RouteEvent {
  type: "route";
  outcome: Outcome;
  agents: string[];
  decided_by: string;
}
export interface ProgressEvent {
  type: "progress";
  channel: string | null;
  message: string;
  percent: number;
}
export interface DeltaEvent {
  type: "delta";
  channel: string | null;
  text: string;
}
export interface ReferencesEvent {
  type: "references";
  channel: string | null;
  items: Reference[];
}
/**
 * 답변 근거 카드. 본문의 `[N](cite:<id>)` 마커가 `id` 를 가리킨다.
 */
export interface Reference {
  id: string;
  source: string;
  title: string;
  snippet: string;
  date: string | null;
  url: string | null;
  score: number | null;
  meta: {
    [k: string]: string;
  };
}
export interface ChannelDoneEvent {
  type: "channel_done";
  channel: string | null;
  status: ChannelStatus;
  usage: Usage;
}
export interface Usage {
  input_tokens: number;
  output_tokens: number;
}
/**
 * 되묻기. `suggestions` 는 바로 보낼 수 있는 재질문, `choices` 는 도메인 버튼.
 */
export interface HitlEvent {
  type: "hitl";
  stage: "rewrite" | "domain";
  message: string;
  suggestions: string[];
  choices: DomainChoice[];
}
export interface DomainChoice {
  domain: string;
  label: string;
  description: string;
}
export interface ErrorEvent {
  type: "error";
  channel: string | null;
  code: ErrorCode;
  message: string;
}
export interface DoneEvent {
  type: "done";
  turn_id: string;
  status: TurnStatus;
  outcome: Outcome | null;
  answers: Answer[];
  hitl: HitlEvent | null;
  recommendations: string[];
  usage: Usage;
  latency_ms: number;
}
export interface Answer {
  channel: string;
  text: string;
  status: ChannelStatus;
  references: Reference[];
}
