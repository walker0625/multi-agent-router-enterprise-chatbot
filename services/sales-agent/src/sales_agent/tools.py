"""영업 자료 검색 도구 `search_sales_knowledge`.

도구 스키마는 Pydantic 인자 모델(`SearchArgs`)에서 생성한다. 손으로 쓴 JSON 스키마와 실행
함수 시그니처를 따로 두면 둘이 어긋나도 알 수 없다. LLM 이 준 날짜·이름은 이 모델로 파싱된
값으로만 필터에 들어간다.

실행 규칙:
- 인자 검증이 실패해도 예외를 올리지 않고 오류 설명을 도구 결과로 돌려준다. 모델이 읽고
  고쳐서 다시 부를 수 있다.
- intent별 결과를 따로 담는다. 한 변수에 이어 담으면 뒤 intent 결과(예: 날짜 필터 없는 검색)가
  앞 결과를 조용히 덮는다.
- 턴 예산(도구 호출·검색 수)이 끝났으면 실행하지 않고 "예산 초과" 결과를 돌려준다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Self

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from chatcore.errors import BudgetExceeded
from chatcore.events import Reference
from chatcore.llm import ToolCall, ToolSpec
from sales_agent.context import RequestContext
from sales_agent.presenters import present
from sales_agent.retrieval import Intent, Item, Retriever

TOOL_NAME = "search_sales_knowledge"

BUDGET_NOTICE = "예산 초과: 이번 질문에 쓸 수 있는 검색 한도를 모두 썼습니다. 지금까지 찾은 결과로 답하세요."


class SearchArgs(BaseModel):
    intents: list[Intent] = Field(
        min_length=1,
        max_length=3,
        description="찾을 자료 종류. account=고객사 현황, contact=고객 담당자, meeting=영업 회의록과 첨부",
    )
    query: str = Field(min_length=1, max_length=200, description="검색어")
    account_name: str | None = Field(default=None, description="질문에 나온 고객사 이름")
    date_from: date | None = Field(default=None, description="회의 날짜 시작(YYYY-MM-DD)")
    date_to: date | None = Field(default=None, description="회의 날짜 끝(YYYY-MM-DD)")
    top_k: int = Field(default=5, description="intent별 결과 수(1~10)")

    @field_validator("intents", mode="before")
    @classmethod
    def _dedupe(cls, value: Any) -> Any:
        # 길이 검사 전에 중복을 지운다. 문자열 목록이 아니면 타입 검증에 맡긴다.
        if isinstance(value, list) and all(isinstance(v, str) for v in value):
            return list(dict.fromkeys(value))
        return value

    @field_validator("top_k")
    @classmethod
    def _clamp(cls, value: int) -> int:
        return max(1, min(10, value))

    @model_validator(mode="after")
    def _order_dates(self) -> Self:
        if self.date_from and self.date_to and self.date_from > self.date_to:
            self.date_from, self.date_to = self.date_to, self.date_from
        return self


def search_tool(description: str) -> ToolSpec:
    return ToolSpec(name=TOOL_NAME, description=description, parameters=SearchArgs.model_json_schema())


@dataclass(frozen=True)
class ToolOutcome:
    content: str  # tool 메시지 본문(JSON)
    evidence: dict[str, Any] | None = None  # 답변 근거. 오류·예산 초과 결과면 None
    references: list[Reference] = field(default_factory=list)


def _dump(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _error(message: str, **extra: Any) -> ToolOutcome:
    return ToolOutcome(content=_dump({"error": message, **extra}))


async def execute(call: ToolCall, ctx: RequestContext, retriever: Retriever) -> ToolOutcome:
    if ctx.budget.exhausted:
        return _error(BUDGET_NOTICE)
    ctx.budget.tool_calls += 1
    if call.name != TOOL_NAME:
        return _error(f"알 수 없는 도구입니다: {call.name}. {TOOL_NAME} 만 쓸 수 있습니다.")
    try:
        args = SearchArgs.model_validate_json(call.arguments)
    except ValidationError as exc:
        details = [
            {"field": ".".join(str(p) for p in e["loc"]), "message": e["msg"]}
            for e in exc.errors(include_url=False, include_input=False)
        ]
        return _error("인자가 올바르지 않습니다. 고쳐서 다시 호출하세요.", details=details)

    account_id = retriever.account_id(args.account_name) if args.account_name else None
    notices = []
    if args.account_name and account_id is None:
        notices.append(f"고객사 '{args.account_name}'을(를) 찾지 못해 이름 조건 없이 검색했습니다.")
    results: dict[Intent, list[Item]] = {}
    for intent in args.intents:
        try:
            found = await retriever.find(
                ctx,
                intent,
                args.query,
                account_id=account_id,
                date_from=args.date_from,
                date_to=args.date_to,
                top_k=args.top_k,
            )
        except BudgetExceeded:
            notices.append(BUDGET_NOTICE)
            break
        results[intent] = list(found)

    presented = present(results)
    evidence: dict[str, Any] = {"query": args.query}
    if account_id:
        evidence["account"] = retriever.account_name(account_id)
    if args.date_from or args.date_to:
        evidence["period"] = [str(d) if d else None for d in (args.date_from, args.date_to)]
    evidence["results"] = presented.results
    if notices:
        evidence["notices"] = notices
    return ToolOutcome(content=_dump(evidence), evidence=evidence, references=presented.references)
