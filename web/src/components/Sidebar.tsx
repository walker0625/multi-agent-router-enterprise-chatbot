// 대화 목록. 날짜 구간(오늘·어제·지난 7일·이전)으로 묶는다.
import type { ChatSummary } from "../api/types";

interface Props {
  chats: ChatSummary[];
  activeId: string | null;
  disabled: boolean;
  onNew: () => void;
  onOpen: (id: string) => void;
  onDelete: (id: string) => void;
}

function bucket(iso: string, now: Date): string {
  const day = 86_400_000;
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const t = new Date(iso).getTime();
  if (t >= startOfToday) return "오늘";
  if (t >= startOfToday - day) return "어제";
  if (t >= startOfToday - 7 * day) return "지난 7일";
  return "이전";
}

export function Sidebar({ chats, activeId, disabled, onNew, onOpen, onDelete }: Props) {
  const now = new Date();
  const groups = new Map<string, ChatSummary[]>();
  for (const c of chats) {
    const key = bucket(c.updated_at, now);
    groups.set(key, [...(groups.get(key) ?? []), c]);
  }
  return (
    <nav className="sidebar" aria-label="대화 목록">
      <button type="button" className="primary block" onClick={onNew} disabled={disabled}>
        + 새 대화
      </button>
      {chats.length === 0 && <p className="muted small">아직 대화가 없습니다.</p>}
      {[...groups].map(([label, items]) => (
        <section key={label}>
          <h2>{label}</h2>
          <ul>
            {items.map((c) => (
              <li key={c.id} className={c.id === activeId ? "active" : ""}>
                <button type="button" className="chat-link" disabled={disabled} onClick={() => onOpen(c.id)}>
                  {c.title || "새 대화"}
                </button>
                <button
                  type="button"
                  className="icon"
                  disabled={disabled}
                  aria-label={`${c.title} 삭제`}
                  onClick={() => onDelete(c.id)}
                >
                  ×
                </button>
              </li>
            ))}
          </ul>
        </section>
      ))}
    </nav>
  );
}
