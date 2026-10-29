"""L2 — 벡터 단일 게이트.

손으로 쓴 대표 질문(앵커)과의 코사인만 본다. top-3 중 τ 이상인 앵커의 도메인이 정확히 하나일
때만 결정한다. 도메인별 점수를 합하거나 가중하지 않는다 — 통과/불통과만 센다.

τ 는 임베더마다 다른 값이다. 보정에 쓴 임베더(`thresholds.yaml` 의 `embedder`)와 지금 임베더가
다르면 L2 를 건너뛴다(skipped). 다른 모델의 τ 를 그대로 쓰면 오류 없이 조용히 오판한다.
"""

from __future__ import annotations

import logging

from chatcore.deadline import Deadline
from chatcore.errors import ProviderError, ProviderTimeout
from chatcore.events import Outcome
from chatcore.llm import Embedder
from chatcore.prompts import VersionedFile
from chatcore.search import Document, InMemoryIndex, embed_documents
from router_service.routing.base import Domain, LayerResult

log = logging.getLogger(__name__)

TOP_K = 3


def gate(top: list[tuple[Domain, float]], tau: float) -> list[Domain]:
    """τ 이상인 도메인 목록. 정확히 하나일 때만 결정한다(호출부 규칙). 보정 스크립트도 같은 함수를 쓴다."""
    return sorted({d for d, score in top if score >= tau})


class VectorGateLayer:
    def __init__(
        self,
        *,
        embedder: Embedder,
        index: InMemoryIndex | None,
        tau: float,
        calibrated_for: str,
        tags: list[str],
    ) -> None:
        self._embedder = embedder
        self._index = index
        self._tau = tau
        self._calibrated_for = calibrated_for
        self._tags = tags

    @classmethod
    async def build(
        cls, anchors: VersionedFile, thresholds: VersionedFile, embedder: Embedder
    ) -> VectorGateLayer:
        tau = float(thresholds.data["tau"])
        calibrated_for = str(thresholds.data["embedder"])
        tags = [anchors.tag, thresholds.tag]
        docs = [
            Document(id=f"anchor-{i}", text=a["text"], fields={"domain": Domain(a["domain"])})
            for i, a in enumerate(anchors.data["anchors"])
        ]
        if embedder.name != calibrated_for:
            log.warning(
                "L2 skipped: tau was calibrated for another embedder",
                extra={"embedder": embedder.name, "calibrated_for": calibrated_for},
            )
            return cls(embedder=embedder, index=None, tau=tau, calibrated_for=calibrated_for, tags=tags)
        await embed_documents(docs, embedder)
        index = InMemoryIndex("route-anchors", docs, filterable=())
        return cls(embedder=embedder, index=index, tau=tau, calibrated_for=calibrated_for, tags=tags)

    @property
    def name(self) -> str:
        return "L2"

    @property
    def tags(self) -> list[str]:
        return self._tags

    async def route(self, query: str, deadline: Deadline) -> LayerResult:
        if self._index is None:
            return LayerResult(
                layer=self.name,
                status="skipped",
                reason="embedder_mismatch",
                detail={"embedder": self._embedder.name, "calibrated_for": self._calibrated_for},
            )
        try:
            [vector] = await deadline.run(self._embedder.embed([query]))
        except (ProviderError, ProviderTimeout) as exc:
            log.warning("L2 embedding failed", extra={"detail": exc.detail})
            return LayerResult(layer=self.name, status="undecided", reason="embed_failed")
        hits = await self._index.search(vector=vector, top_k=TOP_K)
        top = [(Domain(h.doc.fields["domain"]), h.score) for h in hits]
        passed = gate(top, self._tau)
        detail = {
            "top3": [{"domain": d.value, "score": round(s, 4)} for d, s in top],
            "tau": self._tau,
            "embedder": self._embedder.name,
        }
        if len(passed) == 1:
            return LayerResult(
                layer=self.name,
                status="decided",
                outcome=Outcome.SINGLE,
                domains=passed,
                reason="single_domain_above_tau",
                detail=detail,
            )
        reason = "below_tau" if not passed else "multi_domain_above_tau"
        return LayerResult(layer=self.name, status="undecided", reason=reason, detail=detail)
