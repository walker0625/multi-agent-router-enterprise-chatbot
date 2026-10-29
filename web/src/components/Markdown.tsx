// 답변 본문. `[N](cite:<id>)` 링크는 위첨자 번호 버튼으로 바꿔 참조 패널과 연결한다.
import ReactMarkdown, { defaultUrlTransform } from "react-markdown";

interface Props {
  text: string;
  onCite: (refId: string) => void;
}

const keepCite = (url: string) => (url.startsWith("cite:") ? url : defaultUrlTransform(url));

export function Markdown({ text, onCite }: Props) {
  return (
    <div className="markdown">
      <ReactMarkdown
        urlTransform={keepCite}
        components={{
          a({ href, children }) {
            if (href?.startsWith("cite:")) {
              const id = href.slice(5);
              return (
                <button type="button" className="cite" onClick={() => onCite(id)} data-cite={id}>
                  {children}
                </button>
              );
            }
            return (
              <a href={href} target="_blank" rel="noreferrer">
                {children}
              </a>
            );
          },
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}
