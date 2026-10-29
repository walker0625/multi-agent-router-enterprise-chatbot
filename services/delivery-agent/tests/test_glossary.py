import asyncio
from collections.abc import AsyncIterator, Sequence
from typing import Any

import pytest

from chatcore.deadline import Deadline
from chatcore.errors import ProviderError
from chatcore.llm import Completion, FakeChatModel, FakeRequest, Message, StreamChunk, ToolSpec
from chatcore.prompts import VersionedFile
from delivery_agent.fakes import fake_handlers
from delivery_agent.glossary import GlossaryExpander, load_glossary
from delivery_agent.settings import Settings

TERMS = load_glossary(Settings().data_dir / "delivery" / "glossary.json")


def _expander(prompts: VersionedFile, chat: Any = None, timeout_s: float = 3.0) -> GlossaryExpander:
    return GlossaryExpander(
        TERMS, chat or FakeChatModel(fake_handlers(prompts)), prompts, disambiguate_timeout_s=timeout_s
    )


async def test_aliases_are_inserted_right_after_the_term(prompts: VersionedFile) -> None:
    out = await _expander(prompts).expand("CR 승인 현황", Deadline(5))
    assert out.text == "CR(변경요청, 변경 요청서) 승인 현황"


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("cr 승인", "cr(변경요청, 변경 요청서) 승인"),  # 대소문자 무시
        ("킥 오프 회의록", "킥 오프(착수 회의, 착수보고) 회의록"),  # 공백 무시
        ("UAT를 언제 해?", "UAT(사용자 인수 테스트, 인수테스트)를 언제 해?"),  # 조사가 붙어도 매칭
        ("UAT 끝나면 SIT", "UAT(사용자 인수 테스트, 인수테스트) 끝나면 SIT(통합 테스트, 통합테스트)"),
    ],
)
async def test_matching_ignores_case_and_spacing(prompts: VersionedFile, query: str, expected: str) -> None:
    assert (await _expander(prompts).expand(query, Deadline(5))).text == expected


@pytest.mark.parametrize("query", ["CRM 도입 현황", "SITE 점검 일정", "ACR 설정"])
async def test_term_inside_a_longer_word_is_not_matched(prompts: VersionedFile, query: str) -> None:
    assert (await _expander(prompts).expand(query, Deadline(5))).text == query


async def test_ambiguous_term_follows_the_llm_choice(prompts: VersionedFile) -> None:
    expander = _expander(prompts)
    assert (await expander.expand("PO 발주 일정", Deadline(5))).text == "PO(발주서, 구매주문) 발주 일정"
    assert (await expander.expand("스프린트 백로그는 PO 가 정리", Deadline(5))).text == (
        "스프린트 백로그는 PO 가 정리"
    )


def _fail(_req: FakeRequest) -> str:
    raise ProviderError("model down")


async def test_ambiguous_term_is_not_expanded_when_llm_fails(prompts: VersionedFile) -> None:
    chat = FakeChatModel({"delivery.glossary_disambiguate": _fail})
    out = await _expander(prompts, chat).expand("CR 관련 PO 발주", Deadline(5))
    assert out.text == "CR(변경요청, 변경 요청서) 관련 PO 발주"


class _SlowChat:
    name = "slow"

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        task: str,
        deadline: Deadline,
        json_schema: dict[str, Any] | None = None,
        tools: Sequence[ToolSpec] | None = None,
        max_tokens: int | None = None,
    ) -> Completion:
        await asyncio.sleep(5)
        return Completion(text='{"choice": "A"}')

    async def stream(
        self, messages: Sequence[Message], *, task: str, deadline: Deadline, max_tokens: int | None = None
    ) -> AsyncIterator[StreamChunk]:
        yield StreamChunk()


async def test_ambiguous_term_is_not_expanded_on_timeout(prompts: VersionedFile) -> None:
    out = await _expander(prompts, _SlowChat(), timeout_s=0.05).expand("PM 회의", Deadline(5))
    assert out.text == "PM 회의"


async def test_expansion_never_raises(prompts: VersionedFile, monkeypatch: pytest.MonkeyPatch) -> None:
    expander = _expander(prompts)

    def broken(*_args: object) -> None:
        raise RuntimeError("unexpected")

    monkeypatch.setattr(expander._cache, "get", broken)
    assert (await expander.expand("CR 승인 현황", Deadline(5))).text == "CR 승인 현황"
