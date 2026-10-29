"""fake 모드 응답 — API 키 없이도 데모가 실제처럼 돌게 한다.

- `sales.plan`: 첫 호출에서 질문의 단서로 도구 호출을 만든다. 고객사 이름은 계정 목록과
  대조해 넣고, 기간 표현은 시스템 프롬프트의 오늘 날짜를 기준으로 범위를 만든다. 도구 결과를
  받은 뒤의 호출에서는 도구를 부르지 않는다.
- `sales.answer`: 근거 블록의 상위 항목에서 핵심 문장을 엮고 `[n](cite:<cite_id>)` 를 붙인다.
  연락처는 결과에 있는 값(원문 또는 `[비공개]`)을 그대로 쓴다 — 권한 차이가 답변에 그대로
  드러나야 데모가 의미 있다.
"""

from __future__ import annotations

import calendar
import json
import re
from collections.abc import Sequence
from datetime import date
from typing import Any

from chatcore.llm import FakeHandler, FakeReply, FakeRequest, ToolCall
from sales_agent.privacy import HIDDEN
from sales_agent.prompts import EVIDENCE_CLOSE, EVIDENCE_OPEN
from sales_agent.tools import TOOL_NAME

CONTACT_CUES = ("담당자", "연락처", "누구")
MEETING_CUES = ("회의", "미팅", "협상", "견적", "제안")
# 질문이 회의 종류를 콕 집었는데 결과에 그 종류가 없으면(범위 밖 비공개 등) 다른 회의를 답인 척 하지 않는다.
MEETING_KINDS = ("니즈 파악", "제안 설명회", "가격 협상", "기술 검토", "계약 조건 협의")
NOT_FOUND = (
    "요청하신 내용과 관련된 영업 자료를 찾지 못했습니다. 고객사 이름이나 기간을 바꿔 다시 질문해 주세요."
)

_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_YEAR_MONTH = re.compile(r"(\d{4})년\s*(\d{1,2})월")
_MONTH = re.compile(r"(\d{1,2})월")


def make_fake_handlers(account_names: Sequence[str]) -> dict[str, FakeHandler]:
    def plan(req: FakeRequest) -> FakeReply:
        if any(m.role == "tool" for m in req.messages):
            return FakeReply(text="검색 완료")
        question = req.last_user
        intents = []
        if any(cue in question for cue in CONTACT_CUES):
            intents.append("contact")
        if any(cue in question for cue in MEETING_CUES):
            intents.append("meeting")
        args: dict[str, Any] = {"intents": intents or ["account"], "query": question[:200]}
        if account := next((name for name in account_names if name in question), None):
            args["account_name"] = account
        found = _ISO_DATE.search(req.system_text)
        today = date.fromisoformat(found.group()) if found else date.today()
        if period := date_range(question, today):
            args["date_from"], args["date_to"] = (d.isoformat() for d in period)
        call = ToolCall(id="call_1", name=TOOL_NAME, arguments=json.dumps(args, ensure_ascii=False))
        return FakeReply(tool_calls=[call])

    return {"sales.plan": plan, "sales.answer": answer}


def date_range(text: str, today: date) -> tuple[date, date] | None:
    """기간 표현(2025년 11월·11월·지난달·이번 달·올해·작년) → (시작일, 끝일)."""
    if m := _YEAR_MONTH.search(text):
        return _month(int(m[1]), int(m[2]))
    if m := _MONTH.search(text):
        month = int(m[1])
        # 연도 없는 달은 오늘 이전의 가장 가까운 그 달로 본다.
        return _month(today.year if month <= today.month else today.year - 1, month)
    if "지난달" in text or "지난 달" in text:
        year, month = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
        return _month(year, month)
    if "이번 달" in text or "이번달" in text:
        return _month(today.year, today.month)
    if "올해" in text:
        return date(today.year, 1, 1), date(today.year, 12, 31)
    if "작년" in text:
        return date(today.year - 1, 1, 1), date(today.year - 1, 12, 31)
    return None


def _month(year: int, month: int) -> tuple[date, date] | None:
    if not 1 <= month <= 12:
        return None
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


def answer(req: FakeRequest) -> str:
    question, _, _ = req.last_user.partition(EVIDENCE_OPEN)
    accounts, contacts, meetings = _collect(_evidence(req.last_user))
    if not (accounts or contacts or meetings):
        return NOT_FOUND
    cite = _Citations()
    lines: list[str] = []
    for a in accounts[:1]:
        lines.append(
            f"{a['name']}{_topic(a['name'])} {a['industry']} 업종 고객으로, 현재 영업 단계는 "
            f"'{a['status']}'이고 담당 KAM은 {a['kam']}입니다. 주력 제안은 {a['main_offer']}입니다."
            f"{cite(a)}"
        )
    if contacts:
        top = contacts[0]
        lines.append(
            f"{top['account']} {top['department']} {top['name']} {top['title']}({top['role']})의 연락처는 "
            f"전화 {top['phone']}, 메일 {top['email']}입니다.{cite(top)}"
        )
        for c in contacts[1:3]:
            lines.append(
                f"- {c['name']} {c['title']}({c['department']}, {c['role']}): "
                f"전화 {c['phone']}, 메일 {c['email']}{cite(c)}"
            )
        if top["phone"] == HIDDEN:
            lines.append("연락처 원문은 열람 권한이 있는 사용자에게만 표시됩니다.")
    asked = [kind for kind in MEETING_KINDS if kind in question]
    if meetings and asked and not any(m["kind"] in asked for m in meetings):
        lines.append(
            f"열람할 수 있는 자료에서 '{asked[0]}' 회의록은 찾지 못했습니다. 관련 회의록을 정리합니다."
        )
    for i, m in enumerate(meetings[:2]):
        if i == 0:
            lines.append(f"{m['date']} '{m['title']}' 회의 내용입니다. {' '.join(m['excerpts'])}{cite(m)}")
        else:
            lines.append(f"- 관련 회의: {m['date']} '{m['title']}' — {m['excerpts'][0]}{cite(m)}")
        for att in m["attachments"][:1]:
            lines.append(f"- 첨부 {att['filename']}: {att['text']}{cite(att)}")
    return "\n".join(lines)


class _Citations:
    """처음 인용하는 순서대로 번호를 매긴다."""

    def __init__(self) -> None:
        self._numbers: dict[str, int] = {}

    def __call__(self, item: dict[str, Any]) -> str:
        cite_id = item["cite_id"]
        number = self._numbers.setdefault(cite_id, len(self._numbers) + 1)
        return f"[{number}](cite:{cite_id})"


def _evidence(text: str) -> list[dict[str, Any]]:
    start, end = text.rfind(EVIDENCE_OPEN), text.rfind(EVIDENCE_CLOSE)
    if start < 0 or end < start:
        return []
    return json.loads(text[start + len(EVIDENCE_OPEN) : end])


def _collect(evidence: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], ...]:
    """도구 호출 결과를 intent별로 모은다. 같은 항목은 처음 것만 남긴다."""
    buckets: dict[str, list[dict[str, Any]]] = {"account": [], "contact": [], "meeting": []}
    seen: set[str] = set()
    for payload in evidence:
        for intent, items in payload["results"].items():
            for item in items:
                if item["cite_id"] not in seen:
                    seen.add(item["cite_id"])
                    buckets[intent].append(item)
    return buckets["account"], buckets["contact"], buckets["meeting"]


def _topic(word: str) -> str:
    """받침에 맞는 주제 조사(은/는)."""
    last = word[-1]
    if "가" <= last <= "힣":
        return "은" if (ord(last) - ord("가")) % 28 else "는"
    return "은(는)"
