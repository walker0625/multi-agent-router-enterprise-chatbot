"""3단 캐스케이드.

입구에서 한 번 병합한 질의를 레이어에 차례로 넣고, 처음으로 `decided` 가 나온 레이어에서
멈춘다. 레이어끼리는 아무것도 주고받지 않으므로 어느 레이어를 빼도 그대로 동작한다. 모두
정하지 못하면 HITL 이다.

판정 trace 는 한 턴에 JSON 한 줄로 로그에 남긴다. 같은 trace 를 턴 저장소와 평가가 쓴다.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence

from chatcore.agent_api import HistoryTurn
from chatcore.deadline import Deadline
from chatcore.events import Outcome
from chatcore.llm import Usage
from router_service.routing.base import Layer, LayerResult, RouteResult, RouteTrace
from router_service.routing.contextualize import Contextualizer

log = logging.getLogger(__name__)


class Cascade:
    def __init__(
        self,
        layers: Sequence[Layer],
        *,
        contextualizer: Contextualizer | None = None,
        chat_model: str = "",
        embedder: str = "",
    ) -> None:
        self._layers = list(layers)
        self._contextualizer = contextualizer
        self._chat_model = chat_model
        self._embedder = embedder

    async def route(
        self, query: str, deadline: Deadline, *, history: Sequence[HistoryTurn] = ()
    ) -> RouteResult:
        started = time.monotonic()
        tags = [tag for layer in self._layers for tag in layer.tags]
        merged, usage = query, Usage()
        if self._contextualizer is not None:
            tags.append(self._contextualizer.tag)
            merged, usage = await self._contextualizer.rewrite(query, history, deadline)
        results: list[LayerResult] = []
        decided: LayerResult | None = None
        for layer in self._layers:
            result = await layer.route(merged, deadline)
            results.append(result)
            usage += result.usage
            if result.status == "decided":
                decided = result
                break
        route = RouteResult(
            outcome=decided.outcome if decided and decided.outcome else Outcome.HITL,
            domains=decided.domains if decided else [],
            decided_by=decided.layer if decided else "cascade",
            query=merged,
            original_query=query,
            trace=RouteTrace(
                layers=results,
                tags=tags,
                chat_model=self._chat_model,
                embedder=self._embedder,
                elapsed_ms=int((time.monotonic() - started) * 1000),
            ),
            usage=usage,
        )
        log.info("route", extra={"route": route.model_dump(mode="json")})
        return route
