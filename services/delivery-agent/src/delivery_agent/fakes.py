"""fake 모드 응답 — API 키 없이 데모가 그럴듯하게 돌도록 프롬프트에 실린 재료로 답을 만든다.

핸들러는 실제 LLM 과 같은 프롬프트를 받아 그 안의 질문·근거 목록을 읽는다. 그래서 fake 로 돌려도
프롬프트 형식, 근거 번호, 인용 마커가 실제 경로와 같은 모양으로 검증된다.

- `delivery.intent`: YAML 키워드 규칙으로 의도를 고르고, "지난달·이번 달·3월" 같은 표현에서 기간을 만든다.
- `delivery.glossary_disambiguate`: 약어 주변 단어로 뜻을 고른다.
- `delivery.synthesize`: 상위 근거 3개의 핵심 문장을 엮어 첫 줄 결론 + 항목으로 답하고 인용을 단다.
"""

from __future__ import annotations

import calendar
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from chatcore.llm import FakeHandler, FakeRequest
from chatcore.prompts import VersionedFile
from chatcore.search import tokenize
from delivery_agent.intent import keyword_intent

# 약어별 (용어사전 뜻 단서, 다른 뜻 단서). 다른 뜻 단서만 있을 때 B 를 고른다.
_SENSE_CUES: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "PO": (("발주", "구매"), ("백로그", "스프린트", "프로덕트")),
    "PM": (("프로젝트", "담당", "관리"), ("오후", "오전", "시에", "시까지")),
}

_HEADER = re.compile(r"^\[(\d+)\] \((\S+)\) (.+)$", re.M)


def _after(text: str, label: str) -> str:
    """`label: ` 뒤의 값. 질의처럼 여러 줄일 수 있는 값은 템플릿 끝에 두고 끝까지 읽는다."""
    return text.rpartition(f"{label}: ")[2].strip()


def _line(text: str, label: str) -> str | None:
    m = re.search(rf"^{re.escape(label)}: (.*)$", text, re.M)
    return m.group(1).strip() if m else None


def _month(year: int, month: int) -> tuple[date, date]:
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


def relative_period(query: str, today: date) -> tuple[date, date] | None:
    compact = query.replace(" ", "")
    if "지난달" in compact or "저번달" in compact:
        prev = today.replace(day=1) - timedelta(days=1)
        return _month(prev.year, prev.month)
    if "이번달" in compact:
        return _month(today.year, today.month)
    if m := re.search(r"(?<!\d)(1[0-2]|[1-9])월", query):
        month = int(m.group(1))
        return _month(today.year if month <= today.month else today.year - 1, month)
    return None


def _intent(req: FakeRequest, rules: Sequence[Mapping[str, Any]]) -> str:
    text = req.last_user
    query = _after(text, "질문")
    period = relative_period(query, date.fromisoformat(_line(text, "오늘") or ""))
    return json.dumps(
        {
            "intent": keyword_intent(query, rules).value,
            "date_from": period[0].isoformat() if period else None,
            "date_to": period[1].isoformat() if period else None,
        }
    )


def _disambiguate(req: FakeRequest) -> str:
    text = req.last_user
    query, term = _after(text, "질의"), _line(text, "약어") or ""
    primary_cues, other_cues = _SENSE_CUES.get(term, ((), ()))
    other = any(c in query for c in other_cues) and not any(c in query for c in primary_cues)
    return json.dumps({"choice": "B" if other else "A"})


@dataclass(frozen=True)
class _Evidence:
    n: int
    id: str
    kind: str
    date: str
    project_id: str
    title: str
    body: str

    def key_sentence(self, question: str) -> str:
        """질문과 겹치는 낱말이 가장 많은 문장(같으면 앞 문장)."""
        q = set(tokenize(question))
        sentences = re.split(r"(?<=[.!?])\s+", self.body.strip())
        best = max(sentences, key=lambda s: len(q & set(tokenize(s))))
        return re.sub(r"\[[^\]]*\]\s*", "", best).rstrip(".")

    def cited(self, question: str) -> str:
        return f"{self.key_sentence(question)} [{self.n}](cite:{self.id})."

    def label(self) -> str:
        # 본문 첫 줄 앞의 프로젝트 코드와 "[프로젝트명]" 머리를 떼어 제목만 남긴다.
        title = re.sub(r"^(P-\d+\s+)?(\[[^\]]*\]\s*)?", "", self.title)
        return f"{self.date} {self.kind} 「{title}」"


def _evidence(text: str) -> list[_Evidence]:
    section = text.partition("근거 목록:\n")[2].rpartition("\n\n질문: ")[0]
    headers = list(_HEADER.finditer(section))
    items = []
    for i, m in enumerate(headers):
        end = headers[i + 1].start() if i + 1 < len(headers) else len(section)
        title, _, body = section[m.end() : end].strip().partition("\n")
        kind, day, project = m.group(3).split(" | ")
        items.append(_Evidence(int(m.group(1)), m.group(2), kind, day, project.split(" ")[0], title, body))
    return items


def _synthesize(req: FakeRequest) -> str:
    text = req.last_user
    question = _after(text, "질문")
    evidence = _evidence(text)
    # 질문이 프로젝트 코드를 짚으면 그 프로젝트 근거를 앞에 둔다(실제 모델에도 같은 규칙을 준다).
    mentioned = [e for e in evidence if e.project_id in question]
    picked = (mentioned or evidence)[:3]
    lines = []
    if (note := _line(text, "참고")) and (period := re.search(r"\(([^)]*)\)", note)):
        lines.append(
            f"질문하신 기간({period.group(1)})에는 관련 자료가 없어, 기간 조건 없이 찾은 결과를 정리했습니다."
        )
    top = picked[0]
    lines.append(f"가장 관련 높은 자료는 {top.label()}로, {top.cited(question)}")
    if len(picked) > 1:
        lines += ["", "함께 확인된 근거:"]
        lines += [f"- {e.label()}: {e.cited(question)}" for e in picked[1:]]
    return "\n".join(lines)


def fake_handlers(prompts: VersionedFile) -> dict[str, FakeHandler]:
    rules = prompts.data["intent_keywords"]
    return {
        "delivery.intent": lambda req: _intent(req, rules),
        "delivery.glossary_disambiguate": _disambiguate,
        "delivery.synthesize": _synthesize,
    }
