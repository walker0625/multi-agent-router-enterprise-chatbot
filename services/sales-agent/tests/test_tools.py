import json
from collections.abc import Callable
from datetime import date

import pytest
from pydantic import ValidationError

from chatcore.auth import Principal
from chatcore.llm import ToolCall
from sales_agent.context import RequestContext
from sales_agent.retrieval import Retriever
from sales_agent.tools import BUDGET_NOTICE, TOOL_NAME, SearchArgs, execute, search_tool


def call(arguments: dict | str, name: str = TOOL_NAME) -> ToolCall:
    raw = arguments if isinstance(arguments, str) else json.dumps(arguments, ensure_ascii=False)
    return ToolCall(id="c1", name=name, arguments=raw)


def test_schema_is_generated_from_args_model() -> None:
    spec = search_tool("설명")
    assert spec.name == TOOL_NAME
    assert spec.parameters == SearchArgs.model_json_schema()
    props = spec.parameters["properties"]
    assert set(props) == {"intents", "query", "account_name", "date_from", "date_to", "top_k"}
    assert props["intents"]["items"]["enum"] == ["account", "contact", "meeting"]


def test_args_are_normalized() -> None:
    args = SearchArgs.model_validate(
        {
            "intents": ["meeting", "account", "meeting", "account"],
            "query": "견적",
            "date_from": "2025-12-31",
            "date_to": "2025-11-01",
            "top_k": 50,
        }
    )
    assert args.intents == ["meeting", "account"]  # 중복 제거(순서 유지)
    assert (args.date_from, args.date_to) == (date(2025, 11, 1), date(2025, 12, 31))  # 뒤집힌 날짜 교정
    assert args.top_k == 10
    assert SearchArgs(intents=["account"], query="x", top_k=0).top_k == 1


@pytest.mark.parametrize(
    "bad",
    [
        {"intents": [], "query": "x"},
        {"intents": ["deal"], "query": "x"},
        {"intents": ["account"], "query": ""},
        {"intents": ["account"], "query": "x" * 201},
        {"intents": ["meeting"], "query": "x", "date_from": "지난달"},
    ],
)
def test_invalid_args_rejected_by_model(bad: dict) -> None:
    with pytest.raises(ValidationError):
        SearchArgs.model_validate(bad)


async def test_invalid_args_become_error_result_not_exception(
    retriever: Retriever, make_ctx: Callable[..., RequestContext], seller: Principal
) -> None:
    ctx = make_ctx(seller)
    outcome = await execute(call({"intents": [], "query": "", "top_k": "many"}), ctx, retriever)
    body = json.loads(outcome.content)
    assert "error" in body
    assert {d["field"] for d in body["details"]} == {"intents", "query", "top_k"}
    assert outcome.evidence is None and outcome.references == []

    broken = await execute(call("{not json"), ctx, retriever)
    assert "error" in json.loads(broken.content)
    assert ctx.budget.searches == 0


async def test_unknown_tool_is_error_result(
    retriever: Retriever, make_ctx: Callable[..., RequestContext], seller: Principal
) -> None:
    outcome = await execute(
        call({"intents": ["account"], "query": "x"}, name="drop_table"), make_ctx(seller), retriever
    )
    assert "error" in json.loads(outcome.content)


async def test_exhausted_budget_returns_budget_result_without_searching(
    retriever: Retriever, make_ctx: Callable[..., RequestContext], seller: Principal
) -> None:
    ctx = make_ctx(seller, max_tool_calls=1)
    first = await execute(call({"intents": ["account"], "query": "한빛유통"}), ctx, retriever)
    assert first.evidence is not None
    second = await execute(call({"intents": ["account"], "query": "새솔물류"}), ctx, retriever)
    assert json.loads(second.content) == {"error": BUDGET_NOTICE}
    assert ctx.budget.tool_calls == 1 and ctx.budget.searches == 1


async def test_search_cap_mid_call_keeps_finished_intents(
    retriever: Retriever, make_ctx: Callable[..., RequestContext], seller: Principal
) -> None:
    ctx = make_ctx(seller, max_searches=2)
    outcome = await execute(
        call({"intents": ["account", "contact", "meeting"], "query": "한빛유통", "account_name": "한빛유통"}),
        ctx,
        retriever,
    )
    assert outcome.evidence is not None
    assert set(outcome.evidence["results"]) == {"account", "contact"}
    assert outcome.evidence["notices"] == [BUDGET_NOTICE]
    assert ctx.budget.exhausted


async def test_unknown_account_name_searches_without_account_filter(
    retriever: Retriever, make_ctx: Callable[..., RequestContext], seller: Principal
) -> None:
    outcome = await execute(
        call({"intents": ["account"], "query": "유통 고객", "account_name": "없는회사"}),
        make_ctx(seller),
        retriever,
    )
    assert outcome.evidence is not None
    assert "account" not in outcome.evidence
    assert "없는회사" in outcome.evidence["notices"][0]
    assert len(outcome.evidence["results"]["account"]) == 5  # 이름 조건 없이 top_k 만큼
