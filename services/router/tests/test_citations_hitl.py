import json
from collections.abc import Callable
from typing import Any

from chatcore.deadline import Deadline
from chatcore.events import Answer, ChannelStatus, Reference
from chatcore.llm import FakeChatModel, FakeRequest
from chatcore.prompts import VersionedFile
from router_service.catalog import Project
from router_service.runner import HitlResponder
from router_service.runner.citations import renumber, strip_markers
from router_service.runner.hitl import resolve_placeholders


def ref(ref_id: str) -> Reference:
    return Reference(id=ref_id, source="mail", title=ref_id)


def test_renumber_in_display_order_across_channels() -> None:
    answers = [
        Answer(
            channel="delivery",
            text="지연 14일 [2](cite:ML-2). 원인은 사양 변경 [1](cite:ML-1). 다시 [2](cite:ML-2).",
            status=ChannelStatus.OK,
            references=[ref("ML-1"), ref("ML-2"), ref("ML-9")],
        ),
        Answer(
            channel="sales",
            text="10% 인하 요청 [1](cite:meeting:M-8). 없는 근거 [2](cite:ghost).",
            status=ChannelStatus.OK,
            references=[ref("meeting:M-8")],
        ),
    ]
    delivery, sales = renumber(answers)
    assert delivery.text == "지연 14일 [1](cite:ML-2). 원인은 사양 변경 [2](cite:ML-1). 다시 [1](cite:ML-2)."
    assert [r.id for r in delivery.references] == ["ML-2", "ML-1", "ML-9"]  # 인용 순, 미인용은 뒤
    assert sales.text == "10% 인하 요청 [3](cite:meeting:M-8). 없는 근거 ."  # 카드에 없는 ID 는 지운다
    assert strip_markers(sales.text) == "10% 인하 요청 . 없는 근거 ."


PROJECTS = {
    "P-101": Project(id="P-101", name="한빛유통 물류센터 WMS 고도화", account_name="한빛유통"),
    "P-104": Project(id="P-104", name="대성정밀 MES 구축", account_name="대성정밀"),
}


def test_placeholders_resolve_only_within_allowed_projects() -> None:
    allowed = {"P-101": PROJECTS["P-101"]}
    assert (
        resolve_placeholders("{unit:P-101} 일정 알려줘", allowed)
        == "한빛유통 물류센터 WMS 고도화 일정 알려줘"
    )
    assert resolve_placeholders("{unit:P-104} 일정 알려줘", allowed) is None
    assert resolve_placeholders("{unit:P-101}와 {unit:P-999} 비교", allowed) is None
    assert resolve_placeholders("사용법 알려줘", allowed) == "사용법 알려줘"


async def test_hitl_drops_unauthorized_suggestions_and_falls_back(make_settings: Callable[..., Any]) -> None:
    prompts = VersionedFile.load(make_settings().prompts_path)
    leaking = json.dumps({"suggestions": ["{unit:P-104} 결함 현황", "{unit:P-104} 일정"]})
    seen: list[FakeRequest] = []

    def handler(req: FakeRequest) -> str:
        seen.append(req)
        return leaking

    responder = HitlResponder(FakeChatModel({"hitl.suggest": handler}), prompts, PROJECTS)
    event, _ = await responder.respond("rewrite", "그거 어떻게 됐어?", ["P-101"], Deadline(5))
    assert event.suggestions == prompts.data["hitl_fallback_suggestions"]
    assert "P-104" not in seen[0].last_user and "{unit:P-101}" in seen[0].last_user
    assert [c.domain for c in event.choices] == ["delivery", "sales", "general"]


async def test_hitl_domain_stage_has_buttons_only(make_settings: Callable[..., Any]) -> None:
    chat = FakeChatModel()
    responder = HitlResponder(chat, VersionedFile.load(make_settings().prompts_path), PROJECTS)
    event, _ = await responder.respond("domain", "그거", ["P-101"], Deadline(5))
    assert event.stage == "domain" and event.suggestions == [] and len(event.choices) == 3
    assert chat.calls == []
