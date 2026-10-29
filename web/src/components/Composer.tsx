// 입력창. Enter 로 보내고 Shift+Enter 로 줄을 바꾼다. 답변 중에는 중지 버튼으로 바뀐다.
import { useState } from "react";

interface Props {
  streaming: boolean;
  onSend: (query: string) => void;
  onStop: () => void;
}

export function Composer({ streaming, onSend, onStop }: Props) {
  const [text, setText] = useState("");
  const send = () => {
    const q = text.trim();
    if (!q || streaming) return;
    onSend(q);
    setText("");
  };
  return (
    <form
      className="composer"
      onSubmit={(e) => {
        e.preventDefault();
        send();
      }}
    >
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
            e.preventDefault();
            send();
          }
        }}
        placeholder="영업·프로젝트 수행·사용법을 물어보세요"
        rows={2}
        maxLength={2000}
        aria-label="질문 입력"
      />
      {streaming ? (
        <button type="button" className="danger" onClick={onStop}>
          중지
        </button>
      ) : (
        <button type="submit" className="primary" disabled={!text.trim()}>
          보내기
        </button>
      )}
    </form>
  );
}
