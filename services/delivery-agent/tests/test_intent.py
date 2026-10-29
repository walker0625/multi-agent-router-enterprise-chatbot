import json
from datetime import date

import pytest

from chatcore.deadline import Deadline
from chatcore.errors import ContentFiltered, ProviderError
from chatcore.llm import FakeChatModel, FakeRequest
from chatcore.prompts import VersionedFile
from delivery_agent.fakes import fake_handlers, relative_period
from delivery_agent.intent import Intent, IntentClassifier, IntentResult

TODAY = date(2026, 10, 29)


def _fail(_req: FakeRequest) -> str:
    raise ProviderError("model down")


async def _classify(chat: FakeChatModel, prompts: VersionedFile, query: str) -> IntentResult:
    result, _ = await IntentClassifier(chat, prompts).classify(query, today=TODAY, deadline=Deadline(5))
    return result


async def test_llm_result_carries_intent_and_period(prompts: VersionedFile) -> None:
    result = await _classify(FakeChatModel(fake_handlers(prompts)), prompts, "지난달 장애 보고 있었어?")
    assert result == IntentResult(Intent.ISSUE, date(2026, 9, 1), date(2026, 9, 30))


@pytest.mark.parametrize("reply", [None, "not json", '{"intent": "unknown"}'])
async def test_keyword_fallback_when_llm_fails(prompts: VersionedFile, reply: str | None) -> None:
    handler = _fail if reply is None else (lambda _req: reply)
    chat = FakeChatModel({"delivery.intent": handler})
    result = await _classify(chat, prompts, "CR 승인 현황 정리해줘")
    assert result == IntentResult(Intent.CHANGE)  # 폴백은 의도만 고르고 기간은 비운다


async def test_unparseable_dates_are_dropped_individually(prompts: VersionedFile) -> None:
    reply = json.dumps({"intent": "schedule", "date_from": "지난달", "date_to": "2026-09-30"})
    chat = FakeChatModel({"delivery.intent": lambda _req: reply})
    result = await _classify(chat, prompts, "지난달 일정")
    assert result == IntentResult(Intent.SCHEDULE, None, date(2026, 9, 30))


async def test_content_filter_is_not_swallowed_by_the_fallback(prompts: VersionedFile) -> None:
    with pytest.raises(ContentFiltered):
        await _classify(FakeChatModel(fake_handlers(prompts)), prompts, "#blocked 일정")


async def test_results_are_cached_per_query(prompts: VersionedFile) -> None:
    chat = FakeChatModel(fake_handlers(prompts))
    classifier = IntentClassifier(chat, prompts)
    first = await classifier.classify("UAT 결과", today=TODAY, deadline=Deadline(5))
    second = await classifier.classify("UAT 결과", today=TODAY, deadline=Deadline(5))
    assert first[0] == second[0] == IntentResult(Intent.TEST)
    assert len(chat.calls) == 1


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("지난달 장애", (date(2026, 9, 1), date(2026, 9, 30))),
        ("이번 달 기성", (date(2026, 10, 1), date(2026, 10, 31))),
        ("3월 일정 지연", (date(2026, 3, 1), date(2026, 3, 31))),
        ("12월 검수", (date(2025, 12, 1), date(2025, 12, 31))),  # 아직 오지 않은 달은 작년
        ("CR 현황", None),
    ],
)
def test_fake_relative_period(query: str, expected: tuple[date, date] | None) -> None:
    assert relative_period(query, TODAY) == expected
