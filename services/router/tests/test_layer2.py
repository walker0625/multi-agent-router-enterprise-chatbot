from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from chatcore.deadline import Deadline
from chatcore.events import Outcome
from chatcore.llm import HashingEmbedder
from chatcore.prompts import VersionedFile
from router_service.routing import Domain, VectorGateLayer


class TableEmbedder:
    """문장마다 정해 둔 벡터를 돌려준다. 코사인 값을 정확히 통제하려고 쓴다."""

    def __init__(self, table: dict[str, list[float]], name: str = "table-3") -> None:
        self._table = table
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    @property
    def dim(self) -> int:
        return 3

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._table[t] for t in texts]


ANCHORS = {
    "version": 1,
    "anchors": [
        {"text": "수행 앵커", "domain": "delivery"},
        {"text": "영업 앵커", "domain": "sales"},
        {"text": "일반 앵커", "domain": "general"},
    ],
}
TABLE = {
    "수행 앵커": [1.0, 0.0, 0.0],
    "영업 앵커": [0.0, 1.0, 0.0],
    "일반 앵커": [0.0, 0.0, 1.0],
    "질문": [0.6, 0.8, 0.0],  # 수행 0.6, 영업 0.8, 일반 0
}


async def build(
    write_yaml: Callable[[str, dict[str, Any]], Path], tau: float, *, embedder_name: str = "table-3"
) -> VectorGateLayer:
    anchors = VersionedFile.load(write_yaml("anchors.yaml", ANCHORS))
    thresholds = VersionedFile.load(
        write_yaml(
            "thresholds.yaml", {"version": 1, "tau": tau, "embedder": "table-3", "calibrated_on": "test"}
        )
    )
    return await VectorGateLayer.build(anchors, thresholds, TableEmbedder(TABLE, embedder_name))


async def test_score_at_tau_passes_and_just_above_does_not(
    write_yaml: Callable[[str, dict[str, Any]], Path],
) -> None:
    at = await (await build(write_yaml, tau=0.8)).route("질문", Deadline(5))
    assert at.status == "decided" and at.outcome is Outcome.SINGLE and at.domains == [Domain.SALES]
    assert at.detail["tau"] == 0.8 and len(at.detail["top3"]) == 3

    above = await (await build(write_yaml, tau=0.81)).route("질문", Deadline(5))
    assert above.status == "undecided" and above.reason == "below_tau"


async def test_two_domains_above_tau_is_undecided(write_yaml: Callable[[str, dict[str, Any]], Path]) -> None:
    result = await (await build(write_yaml, tau=0.5)).route("질문", Deadline(5))
    assert result.status == "undecided" and result.reason == "multi_domain_above_tau"
    assert {t["domain"] for t in result.detail["top3"][:2]} == {"delivery", "sales"}


async def test_embedder_mismatch_skips_layer(write_yaml: Callable[[str, dict[str, Any]], Path]) -> None:
    layer = await build(write_yaml, tau=0.5, embedder_name="other-model")
    result = await layer.route("질문", Deadline(5))
    assert result.status == "skipped" and result.reason == "embedder_mismatch"
    assert result.detail == {"embedder": "other-model", "calibrated_for": "table-3"}


async def test_shipped_threshold_is_for_fake_embedder(make_settings: Callable[..., Any]) -> None:
    # 배포한 τ 는 eval calibrate-l2 가 fake 임베더(hashing-512)로 정한 값이다. 같은 임베더면 건너뛰지 않는다.
    routing = make_settings().routing_dir
    layer = await VectorGateLayer.build(
        VersionedFile.load(routing / "anchors.yaml"),
        VersionedFile.load(routing / "thresholds.yaml"),
        HashingEmbedder(512),
    )
    near = await layer.route("운임 정산 장애 재발 방지 대책 알려줘", Deadline(5))
    assert near.status != "skipped"
    vague = await layer.route("그거 어떻게 됐어?", Deadline(5))
    assert vague.status == "undecided"
