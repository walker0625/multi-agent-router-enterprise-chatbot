"""리랭커 포트와 구현.

리랭커는 1차 검색이 고른 후보만 다시 매긴다. 점수는 0~1 이고 입력 문서 순서와 같은
순서로 돌려준다. 리랭크가 실패하면 호출부가 1차 순위로 돌아간다(여기서 삼키지 않는다).
토큰 사용량은 점수와 함께 돌려준다 — 리랭커 객체에 쌓아 두면 동시 요청끼리 섞인다.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

from chatcore.deadline import Deadline
from chatcore.errors import ProviderError
from chatcore.llm.ports import ChatModel
from chatcore.llm.types import Usage, system, user
from chatcore.search.index import Document, tokenize


@dataclass
class Reranked:
    scores: list[float]
    usage: Usage = field(default_factory=Usage)


class Reranker(Protocol):
    async def rerank(self, query: str, docs: Sequence[Document], *, deadline: Deadline) -> Reranked: ...


class LexicalReranker:
    """질의 토큰 중 문서에 나온 비율. 오프라인 기본값."""

    async def rerank(self, query: str, docs: Sequence[Document], *, deadline: Deadline) -> Reranked:
        deadline.check()
        q = set(tokenize(query))
        if not q:
            return Reranked([0.0] * len(docs))
        return Reranked([len(q & set(tokenize(d.text))) / len(q) for d in docs])


_SCHEMA = {
    "type": "object",
    "properties": {
        "scores": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "i": {"type": "integer"},
                    "score": {"type": "integer", "minimum": 0, "maximum": 3},
                },
                "required": ["i", "score"],
            },
        }
    },
    "required": ["scores"],
}

_INSTRUCTION = (
    "질문에 답하는 데 각 문서가 얼마나 직접 쓸모 있는지 0~3 정수로 매겨라. "
    "3=질문에 바로 답함, 2=핵심 근거 일부, 1=주제만 관련, 0=무관. JSON 으로만 답한다."
)


class LLMReranker:
    def __init__(self, chat: ChatModel, *, task: str = "rerank", snippet_chars: int = 400) -> None:
        self._chat = chat
        self._task = task  # 호출하는 서비스의 라벨(예: delivery.rerank)로 사용량을 집계한다
        self._snippet_chars = snippet_chars

    async def rerank(self, query: str, docs: Sequence[Document], *, deadline: Deadline) -> Reranked:
        listing = "\n".join(f"[{i}] {d.text[: self._snippet_chars]}" for i, d in enumerate(docs))
        result = await self._chat.complete(
            [system(_INSTRUCTION), user(f"질문: {query}\n\n문서:\n{listing}")],
            task=self._task,
            deadline=deadline,
            json_schema=_SCHEMA,
        )
        try:
            items = json.loads(result.text)["scores"]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise ProviderError("reranker returned malformed JSON") from exc
        scores = [0.0] * len(docs)
        for item in items:
            i, s = item.get("i"), item.get("score")
            if isinstance(i, int) and 0 <= i < len(docs) and isinstance(s, int):
                scores[i] = max(0, min(3, s)) / 3
        return Reranked(scores, result.usage)
