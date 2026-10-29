// 한 턴(질문 + 답변 묶음). 채널별 스트림, 라우팅 결과, 되묻기, 후속 질문을 보여 준다.
import type { HitlEvent } from "../api/contract";
import { LAYER_LABEL, OUTCOME_LABEL, STATUS_LABEL, channelLabel } from "../labels";
import type { ChannelView, TurnView } from "../state/turn";
import { Markdown } from "./Markdown";

interface Props {
  turn: TurnView;
  busy: boolean;
  onCite: (turnKey: string, refId: string) => void;
  onAsk: (query: string) => void;
  onChooseDomain: (query: string, domain: string) => void;
  onRegenerate: (turn: TurnView) => void;
}

export function TurnBlock({ turn, busy, onCite, onAsk, onChooseDomain, onRegenerate }: Props) {
  const streaming = turn.status === "streaming";
  const finished = !streaming && turn.turnId && turn.status !== "hitl";
  return (
    <article className="turn" data-testid="turn" data-status={turn.status}>
      <div className="bubble user">{turn.query}</div>
      <div className="assistant">
        <RouteBadge turn={turn} />
        {streaming && turn.channels.length === 0 && !turn.hitl && (
          <Progress message={turn.progress?.message ?? "준비 중"} percent={turn.progress?.percent ?? 5} />
        )}
        {turn.channels.map((c) => (
          <Channel key={c.channel} channel={c} onCite={(id) => onCite(turn.key, id)} />
        ))}
        {turn.hitl && (
          <HitlCard
            hitl={turn.hitl}
            disabled={busy}
            onAsk={onAsk}
            onChoose={(domain) => onChooseDomain(turn.query, domain)}
          />
        )}
        {turn.error && (
          <p className="notice error" role="alert">
            {turn.error.message}
          </p>
        )}
        {turn.status in STATUS_LABEL && <p className="notice muted">{STATUS_LABEL[turn.status]}</p>}
        {turn.recommendations.length > 0 && (
          <div className="chips" aria-label="이어서 물어보기">
            {turn.recommendations.map((q) => (
              <button key={q} type="button" className="chip" disabled={busy} onClick={() => onAsk(q)}>
                {q}
              </button>
            ))}
          </div>
        )}
        {finished && (
          <footer className="turn-footer">
            <button type="button" className="link" disabled={busy} onClick={() => onRegenerate(turn)}>
              다시 생성
            </button>
            {turn.latencyMs !== undefined && <span>{(turn.latencyMs / 1000).toFixed(1)}초</span>}
          </footer>
        )}
      </div>
    </article>
  );
}

function RouteBadge({ turn }: { turn: TurnView }) {
  if (!turn.route) return null;
  const { outcome, agents, decided_by } = turn.route;
  return (
    <div className="route" data-testid="route" data-outcome={outcome}>
      <span className={`badge outcome-${outcome.toLowerCase()}`}>{OUTCOME_LABEL[outcome] ?? outcome}</span>
      {agents.map((a) => (
        <span key={a} className="badge">
          {channelLabel(a)}
        </span>
      ))}
      {decided_by && <span className="badge subtle">{LAYER_LABEL[decided_by] ?? decided_by}</span>}
    </div>
  );
}

function Channel({ channel, onCite }: { channel: ChannelView; onCite: (refId: string) => void }) {
  const waiting = channel.status === "streaming" && !channel.text;
  return (
    <section className={`channel ch-${channel.channel}`} data-testid={`channel-${channel.channel}`}>
      <header>
        <h3>{channelLabel(channel.channel)}</h3>
        {channel.status in STATUS_LABEL && (
          <span className={`status st-${channel.status}`}>{STATUS_LABEL[channel.status]}</span>
        )}
        {channel.references.length > 0 && <span className="muted">근거 {channel.references.length}건</span>}
      </header>
      {waiting && channel.progress && <Progress {...channel.progress} />}
      {channel.text && <Markdown text={channel.text} onCite={onCite} />}
      {channel.error && <p className="notice error">{channel.error.message}</p>}
    </section>
  );
}

function Progress({ message, percent }: { message: string; percent: number }) {
  return (
    <div className="progress" role="status">
      <div className="bar">
        <div style={{ width: `${Math.max(5, percent)}%` }} />
      </div>
      <span>{message}</span>
    </div>
  );
}

function HitlCard(props: {
  hitl: HitlEvent;
  disabled: boolean;
  onAsk: (q: string) => void;
  onChoose: (domain: string) => void;
}) {
  const { hitl, disabled, onAsk, onChoose } = props;
  return (
    <section className="hitl" data-testid="hitl" data-stage={hitl.stage}>
      <p>{hitl.message}</p>
      {hitl.suggestions.length > 0 && (
        <div className="hitl-group">
          <h4>이렇게 물어보시겠어요?</h4>
          {hitl.suggestions.map((s) => (
            <button key={s} type="button" className="suggestion" disabled={disabled} onClick={() => onAsk(s)}>
              {s}
            </button>
          ))}
        </div>
      )}
      {hitl.choices.length > 0 && (
        <div className="hitl-group">
          <h4>찾을 영역을 골라 주세요</h4>
          <div className="choices">
            {hitl.choices.map((c) => (
              <button
                key={c.domain}
                type="button"
                className="choice"
                disabled={disabled}
                onClick={() => onChoose(c.domain)}
              >
                <strong>{c.label}</strong>
                <span>{c.description}</span>
              </button>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}
