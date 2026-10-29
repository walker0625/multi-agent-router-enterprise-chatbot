// 답변 지침(개인 시스템 프롬프트). 말투·형식에만 반영되고 권한·규칙은 바꾸지 못한다.
import { useEffect, useState } from "react";
import { api } from "../api/http";
import type { UserSettings } from "../api/types";

export function SettingsModal({ token, onClose }: { token: string; onClose: () => void }) {
  const [text, setText] = useState("");
  const [state, setState] = useState<"loading" | "idle" | "saving" | "error">("loading");

  useEffect(() => {
    api<UserSettings>("/v1/me/settings", { token })
      .then((s) => {
        setText(s.system_prompt ?? "");
        setState("idle");
      })
      .catch(() => setState("error"));
  }, [token]);

  const save = async () => {
    setState("saving");
    try {
      await api("/v1/me/settings", { method: "PUT", token, body: { system_prompt: text.trim() || null } });
      onClose();
    } catch {
      setState("error");
    }
  };

  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true" aria-label="답변 지침">
      <div className="modal">
        <h2>답변 지침</h2>
        <p className="muted small">예: "결론을 먼저 한 줄로 쓰고, 표로 정리해 줘." 권한이나 검색 범위는 바뀌지 않습니다.</p>
        <textarea value={text} onChange={(e) => setText(e.target.value)} rows={6} maxLength={2000} />
        {state === "error" && <p className="notice error">저장하지 못했습니다.</p>}
        <div className="modal-actions">
          <button type="button" onClick={onClose}>
            취소
          </button>
          <button type="button" className="primary" onClick={save} disabled={state !== "idle"}>
            저장
          </button>
        </div>
      </div>
    </div>
  );
}
