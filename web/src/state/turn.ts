// 스트림 이벤트를 화면 모델(TurnView)로 접는다. 저장된 턴(done payload)도 같은 모델로 복원한다.
import type {
  ChannelStatus,
  DoneEvent,
  ErrorEvent,
  HitlEvent,
  Reference,
  RouteEvent,
  StreamEvent,
  TurnStatus,
} from "../api/contract";

export interface ChannelView {
  channel: string;
  text: string;
  status: ChannelStatus | "streaming";
  progress?: { message: string; percent: number };
  references: Reference[];
  error?: { code: string; message: string };
}

export interface TurnView {
  key: string; // 화면 키. 서버 turn_id 가 오기 전에도 필요하다.
  turnId?: string;
  query: string;
  status: TurnStatus | "streaming";
  route?: RouteEvent;
  progress?: { message: string; percent: number };
  channels: ChannelView[];
  hitl?: HitlEvent;
  recommendations: string[];
  error?: ErrorEvent;
  latencyMs?: number;
}

export function newTurn(key: string, query: string): TurnView {
  return { key, query, status: "streaming", channels: [], recommendations: [] };
}

function withChannel(turn: TurnView, name: string, update: (c: ChannelView) => ChannelView): TurnView {
  const exists = turn.channels.some((c) => c.channel === name);
  const base: ChannelView = { channel: name, text: "", status: "streaming", references: [] };
  const channels = exists
    ? turn.channels.map((c) => (c.channel === name ? update(c) : c))
    : [...turn.channels, update(base)];
  return { ...turn, channels };
}

export function applyEvent(turn: TurnView, event: StreamEvent): TurnView {
  switch (event.type) {
    case "turn":
      return { ...turn, turnId: event.turn_id };
    case "route":
      return { ...turn, route: event };
    case "progress":
      if (!event.channel) return { ...turn, progress: { message: event.message, percent: event.percent } };
      return withChannel(turn, event.channel, (c) => ({ ...c, progress: event }));
    case "references":
      return withChannel(turn, event.channel ?? "answer", (c) => ({ ...c, references: event.items }));
    case "delta":
      return withChannel(turn, event.channel ?? "answer", (c) => ({ ...c, text: c.text + event.text }));
    case "channel_done":
      return withChannel(turn, event.channel ?? "answer", (c) => ({ ...c, status: event.status }));
    case "error":
      if (!event.channel) return { ...turn, error: event };
      return withChannel(turn, event.channel, (c) => ({
        ...c,
        status: "error",
        error: { code: event.code, message: event.message },
      }));
    case "hitl":
      return { ...turn, hitl: event };
    case "done":
      return applyDone(turn, event);
  }
}

// done 이 최종본이다. 인용 번호가 화면 순서대로 다시 매겨져 있으므로 스트리밍 중 본문을 대체한다.
export function applyDone(turn: TurnView, done: DoneEvent): TurnView {
  const channels: ChannelView[] = done.answers.map((a) => {
    const live = turn.channels.find((c) => c.channel === a.channel);
    return { channel: a.channel, text: a.text, status: a.status, references: a.references, error: live?.error };
  });
  for (const c of turn.channels) {
    if (!channels.some((x) => x.channel === c.channel)) {
      channels.push({ ...c, status: c.status === "streaming" ? "error" : c.status });
    }
  }
  return {
    ...turn,
    turnId: done.turn_id,
    status: done.status,
    channels,
    hitl: done.hitl ?? turn.hitl,
    recommendations: done.recommendations,
    latencyMs: done.latency_ms,
    progress: undefined,
  };
}

export function fromStored(key: string, query: string, done: DoneEvent | null, status: TurnStatus): TurnView {
  const turn = newTurn(key, query);
  if (!done) return { ...turn, status };
  const restored = applyDone(turn, done);
  if (done.outcome) {
    restored.route = {
      type: "route",
      outcome: done.outcome,
      agents: done.answers.map((a) => a.channel).filter((c) => c !== "summary"),
      decided_by: "",
    };
  }
  return restored;
}
