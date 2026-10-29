from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from chatcore.deadline import Deadline
from chatcore.events import Outcome
from router_service.routing import Domain, KeywordLayer

KEYWORDS: dict[str, Any] = {
    "version": 1,
    "domains": {
        "delivery": {"exact": ["CR", "UAT"], "partial": ["일정 지연", "기성"]},
        "sales": {"exact": ["KAM"], "partial": ["견적", "영업 단계"]},
        "general": {"exact": [], "partial": ["사용법"]},
    },
    "multi_part_cues": ["그리고", "같이 알려"],
}


@pytest.fixture
def layer(write_yaml: Callable[[str, dict[str, Any]], Path]) -> KeywordLayer:
    return KeywordLayer.load(write_yaml("keywords.yaml", KEYWORDS))


async def route(layer: KeywordLayer, query: str):
    return await layer.route(query, Deadline(5))


@pytest.mark.parametrize("query", ["CR 현황 알려줘", "CR이 몇 건이야?", "cr-96 승인됐어?", "이번 UAT는?"])
async def test_exact_matches_with_boundary_and_particles(layer: KeywordLayer, query: str) -> None:
    result = await route(layer, query)
    assert result.status == "decided"
    assert result.outcome is Outcome.SINGLE and result.domains == [Domain.DELIVERY]


async def test_exact_does_not_match_inside_a_longer_word(layer: KeywordLayer) -> None:
    result = await route(layer, "CRM 도입 사례 알려줘")
    assert result.status == "undecided" and result.reason == "no_match"


async def test_partial_ignores_spaces_and_particles(layer: KeywordLayer) -> None:
    assert (await route(layer, "배차 엔진 일정지연 원인은?")).domains == [Domain.DELIVERY]
    assert (await route(layer, "한빛유통 영업단계랑 담당 KAM은?")).domains == [Domain.SALES]
    assert (await route(layer, "기성고 청구 금액")).domains == [Domain.DELIVERY]


async def test_two_domains_are_ambiguous_not_parallel(layer: KeywordLayer) -> None:
    result = await route(layer, "견적 금액과 기성 금액 비교")
    assert result.status == "undecided" and result.reason == "ambiguous"
    assert result.outcome is None
    assert set(result.detail["matched"]) == {"delivery", "sales"}


async def test_multi_part_cue_blocks_single_decision(layer: KeywordLayer) -> None:
    result = await route(layer, "견적 내용이랑 진행 상황을 같이 알려줘")
    assert result.status == "undecided" and result.reason == "multi_part"
    assert result.detail["cues"] == ["같이 알려"]


async def test_duplicate_keyword_across_domains_is_rejected(
    write_yaml: Callable[[str, dict[str, Any]], Path],
) -> None:
    bad = {**KEYWORDS, "domains": {**KEYWORDS["domains"], "sales": {"exact": ["cr"], "partial": []}}}
    with pytest.raises(ValueError, match="함께 있습니다"):
        KeywordLayer.load(write_yaml("bad.yaml", bad))


async def test_unknown_domain_is_rejected(write_yaml: Callable[[str, dict[str, Any]], Path]) -> None:
    bad = {**KEYWORDS, "domains": {"hr": {"exact": ["휴가"]}}}
    with pytest.raises(ValueError):
        KeywordLayer.load(write_yaml("bad.yaml", bad))


async def test_shipped_keywords_route_demo_questions(make_settings: Callable[..., Any]) -> None:
    layer = KeywordLayer.load(make_settings().routing_dir / "keywords.yaml")
    assert (await route(layer, "P-101 일정 지연 현황 알려줘")).domains == [Domain.DELIVERY]
    assert (await route(layer, "한빛유통 영업 단계랑 담당 KAM 알려줘")).domains == [Domain.SALES]
    assert (await route(layer, "대화 기록은 어디서 봐?")).domains == [Domain.GENERAL]
    multi = await route(layer, "새솔물류 견적 협상 내용과 배차 시스템 프로젝트 진행 상황을 같이 알려줘")
    assert multi.status == "undecided"
