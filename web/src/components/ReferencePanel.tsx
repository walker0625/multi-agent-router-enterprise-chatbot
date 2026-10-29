// 근거 패널. 본문의 인용 번호를 누르면 해당 카드로 스크롤하고 강조한다.
import { useEffect, useRef } from "react";
import type { Reference } from "../api/contract";
import { SOURCE_LABEL, channelLabel, metaEntries } from "../labels";

interface Props {
  groups: { channel: string; references: Reference[] }[];
  activeId: string | null;
  onClose: () => void;
}

export function ReferencePanel({ groups, activeId, onClose }: Props) {
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!activeId || !listRef.current) return;
    const card = listRef.current.querySelector<HTMLElement>(`[data-ref="${CSS.escape(activeId)}"]`);
    card?.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [activeId]);

  return (
    <aside className="refs" aria-label="답변 근거" data-testid="reference-panel">
      <header>
        <h2>답변 근거</h2>
        <button type="button" className="icon" onClick={onClose} aria-label="근거 패널 닫기">
          ×
        </button>
      </header>
      <div ref={listRef} className="refs-list">
        {groups.every((g) => g.references.length === 0) && <p className="muted">근거 자료가 없습니다.</p>}
        {groups
          .filter((g) => g.references.length > 0)
          .map((g) => (
            <section key={g.channel}>
              <h3>{channelLabel(g.channel)}</h3>
              {g.references.map((r, i) => (
                <article key={r.id} className={`ref ${r.id === activeId ? "active" : ""}`} data-ref={r.id}>
                  <div className="ref-head">
                    <span className="num">{i + 1}</span>
                    <span className="badge">{SOURCE_LABEL[r.source] ?? r.source}</span>
                    {r.date && <span className="muted">{r.date}</span>}
                  </div>
                  <h4>{r.title}</h4>
                  {r.snippet && <p>{r.snippet}</p>}
                  {metaEntries(r.meta).length > 0 && (
                    <dl>
                      {metaEntries(r.meta).map(([k, v]) => (
                        <div key={k}>
                          <dt>{k}</dt>
                          <dd>{v}</dd>
                        </div>
                      ))}
                    </dl>
                  )}
                </article>
              ))}
            </section>
          ))}
      </div>
    </aside>
  );
}
