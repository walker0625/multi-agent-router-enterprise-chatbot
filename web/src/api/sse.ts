// POST 로 여는 SSE 스트림. EventSource 는 GET 만 되므로 fetch 본문을 직접 읽는다.
import type { StreamEvent } from "./contract";
import { toApiError } from "./http";

export async function* streamEvents(
  path: string,
  body: unknown,
  token: string,
  signal: AbortSignal,
): AsyncGenerator<StreamEvent> {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
    body: JSON.stringify(body ?? {}),
    signal,
  });
  if (!res.ok || !res.body) throw await toApiError(res);

  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += value;
      let sep: number;
      while ((sep = buffer.indexOf("\n\n")) >= 0) {
        const frame = buffer.slice(0, sep);
        buffer = buffer.slice(sep + 2);
        const data = frame
          .split("\n")
          .filter((line) => line.startsWith("data:"))
          .map((line) => line.slice(5).replace(/^ /, ""))
          .join("\n");
        if (data) yield JSON.parse(data) as StreamEvent; // ": ping" 같은 주석 프레임은 data 가 없다
      }
    }
  } finally {
    reader.releaseLock();
  }
}
