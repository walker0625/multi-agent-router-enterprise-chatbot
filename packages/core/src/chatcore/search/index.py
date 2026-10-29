"""검색 인덱스 포트와 메모리 구현(BM25 + 벡터 + RRF).

데모 데이터 규모(수천 건)에서는 메모리 인덱스로 충분하다. 서비스는 `SearchIndex`
Protocol 에만 의존하므로 외부 검색 엔진으로 바꿔도 호출부는 그대로다.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

import numpy as np

from chatcore.filters import Filter, compile_predicate
from chatcore.llm.ports import Embedder

_WORD = re.compile(r"[0-9a-z가-힣]+")
_HANGUL = re.compile(r"[가-힣]")


def tokenize(text: str) -> list[str]:
    """단어 + 한글 단어의 음절 bigram. 조사가 붙은 어형("일정이", "일정을")끼리도 겹치게 한다."""
    tokens: list[str] = []
    for word in _WORD.findall(text.lower()):
        tokens.append(word)
        if len(word) > 2 and _HANGUL.search(word):
            tokens.extend(word[i : i + 2] for i in range(len(word) - 1))
    return tokens


@dataclass
class Document:
    id: str
    text: str
    fields: dict[str, Any] = field(default_factory=dict)
    vector: list[float] | None = None


@dataclass
class Hit:
    doc: Document
    score: float
    scores: dict[str, float] = field(default_factory=dict)


class SearchIndex(Protocol):
    @property
    def name(self) -> str: ...

    async def search(
        self,
        *,
        text: str | None = None,
        vector: Sequence[float] | None = None,
        filter: Filter | None = None,
        top_k: int = 10,
    ) -> list[Hit]: ...

    async def get(self, ids: Sequence[str]) -> list[Document]: ...


class InMemoryIndex:
    def __init__(
        self,
        name: str,
        docs: Sequence[Document],
        *,
        filterable: Iterable[str],
        rrf_k: int = 60,
        candidate_k: int = 50,
        k1: float = 1.2,
        b: float = 0.75,
    ) -> None:
        self._name = name
        self._docs = list(docs)
        self._by_id = {d.id: d for d in self._docs}
        self._filterable = set(filterable)
        self._rrf_k = rrf_k
        self._candidate_k = candidate_k
        self._k1, self._b = k1, b
        self._tf = [Counter(tokenize(d.text)) for d in self._docs]
        self._len = [sum(tf.values()) for tf in self._tf]
        self._avg_len = (sum(self._len) / len(self._len)) if self._docs else 0.0
        df: Counter[str] = Counter()
        for tf in self._tf:
            df.update(tf.keys())
        n = len(self._docs)
        self._idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}
        vectors = [d.vector for d in self._docs]
        self._matrix = (
            np.asarray(vectors, dtype=np.float32)
            if self._docs and all(v is not None for v in vectors)
            else None
        )

    @property
    def name(self) -> str:
        return self._name

    def __len__(self) -> int:
        return len(self._docs)

    def _bm25(self, query: str, candidates: list[int]) -> dict[int, float]:
        terms = set(tokenize(query))
        scores: dict[int, float] = {}
        for i in candidates:
            tf, length = self._tf[i], self._len[i]
            s = 0.0
            for t in terms:
                f = tf.get(t)
                if not f:
                    continue
                norm = f + self._k1 * (1 - self._b + self._b * length / (self._avg_len or 1))
                s += self._idf.get(t, 0.0) * f * (self._k1 + 1) / norm
            if s > 0:
                scores[i] = s
        return scores

    def _cosine(self, vector: Sequence[float], candidates: list[int]) -> dict[int, float]:
        if self._matrix is None or not candidates:
            return {}
        q = np.asarray(vector, dtype=np.float32)
        q = q / (np.linalg.norm(q) or 1.0)
        sims = self._matrix[candidates] @ q
        return {i: float(s) for i, s in zip(candidates, sims, strict=True)}

    def _top(self, scores: dict[int, float]) -> list[int]:
        return sorted(scores, key=lambda i: scores[i], reverse=True)[: self._candidate_k]

    async def search(
        self,
        *,
        text: str | None = None,
        vector: Sequence[float] | None = None,
        filter: Filter | None = None,
        top_k: int = 10,
    ) -> list[Hit]:
        predicate = compile_predicate(filter, self._filterable)
        candidates = [i for i, d in enumerate(self._docs) if predicate(d.fields)]
        bm25 = self._bm25(text, candidates) if text else {}
        cosine = self._cosine(vector, candidates) if vector is not None else {}
        rankings = [r for r in (self._top(bm25), self._top(cosine)) if r]
        if not rankings:
            return []
        fused: dict[int, float] = {}
        if len(rankings) == 1:
            raw = bm25 or cosine
            order = rankings[0]
            fused = {i: raw[i] for i in order}
        else:
            for ranking in rankings:
                for rank, i in enumerate(ranking):
                    fused[i] = fused.get(i, 0.0) + 1.0 / (self._rrf_k + rank + 1)
            order = sorted(fused, key=lambda i: fused[i], reverse=True)
        hits = []
        for i in order[:top_k]:
            parts = {"fused": fused[i]}
            if i in bm25:
                parts["bm25"] = bm25[i]
            if i in cosine:
                parts["vector"] = cosine[i]
            hits.append(Hit(doc=self._docs[i], score=fused[i], scores=parts))
        return hits

    async def get(self, ids: Sequence[str]) -> list[Document]:
        return [self._by_id[i] for i in ids if i in self._by_id]


async def embed_documents(docs: Sequence[Document], embedder: Embedder) -> None:
    """벡터가 없는 문서에 임베딩을 채운다(인덱스 생성 전에 한 번)."""
    targets = [d for d in docs if d.vector is None]
    if not targets:
        return
    vectors = await embedder.embed([d.text for d in targets])
    for doc, vec in zip(targets, vectors, strict=True):
        doc.vector = vec
