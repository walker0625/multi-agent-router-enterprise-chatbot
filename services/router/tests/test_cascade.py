from collections.abc import Callable
from typing import Any

import pytest

from chatcore.agent_api import HistoryTurn
from chatcore.deadline import Deadline
from chatcore.events import Outcome
from chatcore.llm import FakeChatModel, HashingEmbedder
from chatcore.prompts import VersionedFile
from router_service.fakes import fake_handlers
from router_service.routing import (
    Cascade,
    Contextualizer,
    Domain,
    JudgeLayer,
    KeywordLayer,
    Layer,
    LayerResult,
    VectorGateLayer,
)
from router_service.settings import Settings


class RecordingLayer:
    """받은 질의를 기록하고 정해진 결과를 돌려주는 레이어."""

    def __init__(self, name: str, result: LayerResult) -> None:
        self._name = name
        self._result = result
        self.queries: list[str] = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def tags(self) -> list[str]:
        return [f"{self._name}@test"]

    async def route(self, query: str, deadline: Any) -> LayerResult:
        self.queries.append(query)
        return self._result


def undecided(name: str) -> LayerResult:
    return LayerResult(layer=name, status="undecided", reason="no_match")


async def real_layers(settings: Settings, chat: FakeChatModel) -> dict[str, Layer]:
    routing = settings.routing_dir
    return {
        "L1": KeywordLayer.load(routing / "keywords.yaml"),
        "L2": await VectorGateLayer.build(
            VersionedFile.load(routing / "anchors.yaml"),
            VersionedFile.load(routing / "thresholds.yaml"),
            HashingEmbedder(512),
        ),
        "L3": JudgeLayer.load(chat, routing / "judge.yaml"),
    }


async def test_stops_at_first_decided_layer_and_every_layer_gets_same_query() -> None:
    l1 = RecordingLayer("L1", undecided("L1"))
    l2 = RecordingLayer(
        "L2", LayerResult(layer="L2", status="decided", outcome=Outcome.SINGLE, domains=[Domain.SALES])
    )
    l3 = RecordingLayer("L3", undecided("L3"))
    result = await Cascade([l1, l2, l3]).route("견적 알려줘", Deadline(5))
    assert result.decided_by == "L2" and result.domains == [Domain.SALES]
    assert l1.queries == l2.queries == ["견적 알려줘"] and l3.queries == []
    assert [r.layer for r in result.trace.layers] == ["L1", "L2"]
    assert result.trace.tags == ["L1@test", "L2@test", "L3@test"]


async def test_nothing_decided_is_hitl() -> None:
    skipped = LayerResult(layer="L3", status="skipped", reason="not_configured")
    result = await Cascade([RecordingLayer("L1", undecided("L1")), RecordingLayer("L3", skipped)]).route(
        "그거", Deadline(5)
    )
    assert result.outcome is Outcome.HITL and result.decided_by == "cascade" and result.domains == []
    assert result.trace.layers[-1].status == "skipped"


@pytest.mark.parametrize(
    ("names", "query", "decided_by", "domains"),
    [
        (["L1", "L2", "L3"], "P-101 일정 지연 현황 알려줘", "L1", [Domain.DELIVERY]),
        (["L2", "L3"], "운임 정산 장애 재발 방지 대책 알려줘", "L2", [Domain.DELIVERY]),
        (["L1", "L3"], "다온마트 데이터 플랫폼 PM 이 누구야?", "L3", [Domain.DELIVERY]),
        (["L1", "L2"], "새솔물류 견적 협상과 프로젝트 진행 상황을 같이 알려줘", "cascade", []),
        (["L3"], "한빛유통 영업 단계 알려줘", "L3", [Domain.SALES]),
        ([], "아무 질문", "cascade", []),
    ],
)
async def test_any_layer_can_be_removed(
    settings: Settings,
    chat: FakeChatModel,
    names: list[str],
    query: str,
    decided_by: str,
    domains: list[Domain],
) -> None:
    layers = await real_layers(settings, chat)
    result = await Cascade([layers[n] for n in names]).route(query, Deadline(5))
    assert result.decided_by == decided_by and result.domains == domains


async def test_shipped_cascade_routes_demo_questions(settings: Settings, chat: FakeChatModel) -> None:
    layers = await real_layers(settings, chat)
    cascade = Cascade([layers["L1"], layers["L2"], layers["L3"]])
    parallel = await cascade.route(
        "새솔물류 견적 협상 내용과 배차 시스템 프로젝트 진행 상황을 같이 알려줘", Deadline(5)
    )
    assert parallel.outcome is Outcome.PARALLEL and parallel.decided_by == "L3"
    assert parallel.domains == [Domain.DELIVERY, Domain.SALES]
    vague = await cascade.route("그거 어떻게 됐어?", Deadline(5))
    assert vague.outcome is Outcome.HITL


async def test_contextualize_merges_history_once_at_entry(settings: Settings) -> None:
    chat = FakeChatModel(fake_handlers())
    layer = RecordingLayer("L1", undecided("L1"))
    cascade = Cascade([layer], contextualizer=Contextualizer(chat, VersionedFile.load(settings.prompts_path)))

    first = await cascade.route("그거 어떻게 됐어?", Deadline(5))
    assert first.query == first.original_query == "그거 어떻게 됐어?"
    assert [c.task for c in chat.calls] == []  # 첫 턴에는 부르지 않는다

    history = [HistoryTurn(question="P-101 일정 지연 현황 알려줘", answer="14일 지연입니다.")]
    second = await cascade.route("그거 어떻게 됐어?", Deadline(5), history=history)
    assert second.query == "P-101 일정 지연 현황 어떻게 됐어?"
    assert second.original_query == "그거 어떻게 됐어?"
    assert layer.queries[-1] == second.query
    assert [c.task for c in chat.calls] == ["route.contextualize"]


async def test_contextualize_failure_falls_back_to_original(settings: Settings) -> None:
    def broken(_req: Any) -> str:
        raise RuntimeError("should not be called")

    chat = FakeChatModel({"route.contextualize": broken})
    contextualizer = Contextualizer(chat, VersionedFile.load(settings.prompts_path))
    history = [HistoryTurn(question="P-101 일정", answer="답")]
    query, _ = await contextualizer.rewrite("#blocked 그거는?", history, Deadline(5))
    assert query == "#blocked 그거는?"  # 안전 필터로 실패해도 원문으로 진행
    expired, _ = await contextualizer.rewrite("그거는?", history, Deadline(0))
    assert expired == "그거는?"


def test_layer_protocol_is_satisfied(make_settings: Callable[..., Settings]) -> None:
    layer: Layer = KeywordLayer.load(make_settings().routing_dir / "keywords.yaml")
    assert layer.name == "L1" and layer.tags[0].startswith("keywords@1#")
