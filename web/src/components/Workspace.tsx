// 로그인 이후 화면. 대화 목록·채팅·근거 패널을 묶고 스트림 세션을 관리한다.
//
// 스트림 세션: 새 질문·중지·대화 전환마다 세션 번호를 올리고 이전 요청을 abort 한다.
// 늦게 도착한 이전 세션의 이벤트는 번호가 달라 화면에 반영되지 않는다.
import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api } from "../api/http";
import { streamEvents } from "../api/sse";
import type { ChatDetail, ChatSummary, LoginResponse, Me } from "../api/types";
import { type TurnView, applyEvent, fromStored, newTurn } from "../state/turn";
import { Composer } from "./Composer";
import { ReferencePanel } from "./ReferencePanel";
import { SettingsModal } from "./SettingsModal";
import { Sidebar } from "./Sidebar";
import { TurnBlock } from "./TurnBlock";

interface Props {
  session: LoginResponse;
  onLogout: () => void;
}

let keySeq = 0;
const nextKey = () => `t${++keySeq}`;

export function Workspace({ session, onLogout }: Props) {
  const token = session.token;
  const [me, setMe] = useState<Me | null>(null);
  const [chats, setChats] = useState<ChatSummary[]>([]);
  const [chatId, setChatId] = useState<string | null>(null);
  const [turns, setTurns] = useState<TurnView[]>([]);
  const [streaming, setStreaming] = useState(false);
  const [scope, setScope] = useState<string[]>([]);
  const [refs, setRefs] = useState<{ turnKey: string; activeId: string | null } | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const sessionNo = useRef(0);
  const abortRef = useRef<AbortController | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  const handleError = useCallback(
    (e: unknown) => {
      if (e instanceof ApiError && e.status === 401) onLogout();
      else setNotice(e instanceof Error ? e.message : "오류가 발생했습니다.");
    },
    [onLogout],
  );

  const refreshChats = useCallback(
    () => api<ChatSummary[]>("/v1/chats", { token }).then(setChats).catch(handleError),
    [token, handleError],
  );

  useEffect(() => {
    api<Me>("/v1/me", { token }).then(setMe).catch(handleError);
    refreshChats();
    return () => abortRef.current?.abort();
  }, [token, refreshChats, handleError]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: "end" });
  }, [turns]);

  const cancelStream = () => {
    sessionNo.current += 1;
    abortRef.current?.abort();
    setStreaming(false);
  };

  const openChat = async (id: string) => {
    cancelStream();
    setRefs(null);
    try {
      const detail = await api<ChatDetail>(`/v1/chats/${id}`, { token });
      setChatId(id);
      setTurns(detail.turns.map((t) => fromStored(nextKey(), t.query, t.payload, t.status)));
    } catch (e) {
      handleError(e);
    }
  };

  const newChat = () => {
    cancelStream();
    setChatId(null);
    setTurns([]);
    setRefs(null);
  };

  const deleteChat = async (id: string) => {
    if (!window.confirm("이 대화를 삭제할까요?")) return;
    try {
      await api(`/v1/chats/${id}`, { method: "DELETE", token });
      if (id === chatId) newChat();
      refreshChats();
    } catch (e) {
      handleError(e);
    }
  };

  // 스트림 하나를 끝까지 읽어 turnKey 턴에 반영한다. 다른 세션이 시작되면 조용히 멈춘다.
  const runStream = async (path: string, body: unknown, turnKey: string) => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    const mySession = ++sessionNo.current;
    setStreaming(true);
    setNotice(null);
    try {
      for await (const event of streamEvents(path, body, token, controller.signal)) {
        if (mySession !== sessionNo.current) return;
        setTurns((prev) => prev.map((t) => (t.key === turnKey ? applyEvent(t, event) : t)));
      }
    } catch (e) {
      if (controller.signal.aborted || mySession !== sessionNo.current) return;
      const message = e instanceof Error ? e.message : "연결이 끊겼습니다.";
      setTurns((prev) =>
        prev.map((t) =>
          t.key === turnKey && t.status === "streaming"
            ? { ...t, status: "error", error: { type: "error", code: "INTERNAL", channel: null, message } }
            : t,
        ),
      );
      handleError(e);
    } finally {
      if (mySession === sessionNo.current) {
        setStreaming(false);
        refreshChats();
      }
    }
  };

  const ask = async (query: string, domain?: string) => {
    if (streaming) return;
    let id = chatId;
    try {
      if (!id) {
        const chat = await api<ChatSummary>("/v1/chats", { token, body: { title: query.slice(0, 40) } });
        id = chat.id;
        setChatId(id);
      }
    } catch (e) {
      handleError(e);
      return;
    }
    const turn = newTurn(nextKey(), query);
    setTurns((prev) => [...prev, turn]);
    await runStream(
      `/v1/chats/${id}/turns/stream`,
      { query, domain: domain ?? null, project_ids: scope.length ? scope : null },
      turn.key,
    );
  };

  const regenerate = async (old: TurnView) => {
    if (!chatId || !old.turnId || streaming) return;
    const turn = newTurn(nextKey(), old.query);
    setTurns((prev) => prev.map((t) => (t.key === old.key ? turn : t)));
    setRefs(null);
    await runStream(`/v1/chats/${chatId}/turns/${old.turnId}/regenerate`, {}, turn.key);
  };

  const stop = async () => {
    const active = turns.find((t) => t.status === "streaming");
    if (!chatId || !active?.turnId) {
      cancelStream();
      return;
    }
    try {
      // 서버가 하위 스트림을 정리하고 stopped 로 끝내도록 요청한다. 응답이 오지 않으면 잠시 뒤 끊는다.
      await api(`/v1/chats/${chatId}/turns/${active.turnId}/stop`, { method: "POST", token, body: {} });
      const mySession = sessionNo.current;
      window.setTimeout(() => mySession === sessionNo.current && cancelStream(), 5000);
    } catch {
      cancelStream();
    }
  };

  const refTurn = refs ? turns.find((t) => t.key === refs.turnKey) : undefined;
  const projects = me?.projects ?? [];

  return (
    <div className={`workspace ${refTurn ? "with-refs" : ""}`}>
      <Sidebar
        chats={chats}
        activeId={chatId}
        disabled={streaming}
        onNew={newChat}
        onOpen={openChat}
        onDelete={deleteChat}
      />
      <main className="chat">
        <header className="chat-header">
          <div>
            <strong>{me?.name ?? session.user.name}</strong>
            <span className="muted"> · {me?.team ?? session.user.team}</span>
          </div>
          <div className="header-actions">
            {projects.length > 0 && (
              <label className="scope">
                범위
                <select
                  value={scope[0] ?? ""}
                  onChange={(e) => setScope(e.target.value ? [e.target.value] : [])}
                  aria-label="프로젝트 범위"
                >
                  <option value="">내 프로젝트 전체</option>
                  {projects.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.id} {p.name}
                    </option>
                  ))}
                </select>
              </label>
            )}
            <button type="button" onClick={() => setSettingsOpen(true)}>
              답변 지침
            </button>
            <button type="button" onClick={onLogout}>
              사용자 바꾸기
            </button>
          </div>
        </header>
        <div className="messages">
          {turns.length === 0 && <EmptyState onAsk={ask} />}
          {turns.map((t) => (
            <TurnBlock
              key={t.key}
              turn={t}
              busy={streaming}
              onCite={(turnKey, refId) => setRefs({ turnKey, activeId: refId })}
              onAsk={(q) => ask(q)}
              onChooseDomain={(q, d) => ask(q, d)}
              onRegenerate={regenerate}
            />
          ))}
          <div ref={bottomRef} />
        </div>
        {notice && (
          <p className="notice error floating" role="alert">
            {notice}
          </p>
        )}
        <Composer streaming={streaming} onSend={(q) => ask(q)} onStop={stop} />
      </main>
      {refTurn && (
        <ReferencePanel
          groups={refTurn.channels.map((c) => ({ channel: c.channel, references: c.references }))}
          activeId={refs?.activeId ?? null}
          onClose={() => setRefs(null)}
        />
      )}
      {settingsOpen && <SettingsModal token={token} onClose={() => setSettingsOpen(false)} />}
    </div>
  );
}

const EXAMPLES = [
  "P-101 일정 지연 현황 알려줘",
  "한빛유통 영업 단계랑 담당 KAM 알려줘",
  "새솔물류 견적 협상 내용과 배차 시스템 프로젝트 진행 상황을 같이 알려줘",
  "그거 어떻게 됐어?",
  "답변 근거는 어떻게 확인해?",
];

function EmptyState({ onAsk }: { onAsk: (q: string) => void }) {
  return (
    <div className="empty">
      <h2>무엇을 도와드릴까요?</h2>
      <p className="muted">질문은 3단 라우팅(키워드 → 유사도 → LLM 판정)을 거쳐 알맞은 에이전트로 갑니다.</p>
      <div className="chips">
        {EXAMPLES.map((q) => (
          <button key={q} type="button" className="chip" onClick={() => onAsk(q)}>
            {q}
          </button>
        ))}
      </div>
    </div>
  );
}
