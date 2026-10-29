"""fake 모드 응답(키 없이 데모가 자연스럽게 보이도록).

`FakeChatModel` 에 task 별 핸들러로 등록한다. 핸들러는 실제 프롬프트(prompts.yaml·judge.yaml)가
만든 메시지를 읽어 응답을 만든다 — 프롬프트 형식이 바뀌면 여기도 같이 바뀌어야 한다.
- route.judge: 도메인별 힌트 어휘로 YES/NO 와 근거를 만든다. 힌트는 judge.yaml 의 도메인
  정의(다루는 것)에 맞추고, 골드셋 dev 분할 오답만 보고 보강했다(test 는 보지 않는다).
  흔한 단어(일정·이슈·미팅…) 하나만 걸리면 대상이 없는 질문으로 보고 모두 NO(→ HITL)다.
  두 업무 도메인이 모두 걸리고 복합 단서가 있거나, 둘 다 2개 이상 걸리면 둘 다 YES 다.
- route.contextualize: 지시어가 있거나 질문이 아주 짧으면 직전 질문의 핵심 구절을 붙인다.
- hitl.suggest: 사용자 프로젝트 자리표시자(`{unit:P-101}`)를 쓴 제안 2~3개.
- summary: 채널별 첫 문장을 묶은 요약.
- recommend: 도메인별 템플릿 후속 질문.
- general.answer: FAQ 상위 근거 문장 + 인용 마커.
"""

from __future__ import annotations

import json
import re

from chatcore.llm import FakeHandler, FakeRequest
from router_service.routing import Domain

_LABELS = {Domain.DELIVERY: "프로젝트 수행", Domain.SALES: "영업", Domain.GENERAL: "사용 안내·사내 일반"}

_JUDGE_HINTS: dict[Domain, tuple[str, ...]] = {
    Domain.DELIVERY: (
        "프로젝트", "진행 상황", "진척", "일정", "지연", "공정률", "장애", "이슈", "테스트", "결함",
        "CR", "변경 요청", "기성", "인력", "투입", "검수", "주간보고", "PM", "개발", "산출물",
        "모듈", "인터페이스", "사양", "인덱스", "조치", "공수",
    ),
    Domain.SALES: (
        "영업", "고객사", "KAM", "담당자", "견적", "협상", "제안", "경쟁", "계약", "수주",
        "니즈", "가격", "연락처", "의사결정", "회의록", "미팅", "예산", "할인", "답변서",
        "경영회의", "관심", "구매팀", "대리",
    ),
    Domain.GENERAL: (
        "사용법", "챗봇", "대화", "버튼", "다시 생성", "새로 받", "중지", "답변 지침", "말투", "출장",
        "경비", "영수증", "보안", "노트북", "반출", "USB", "가져가", "규정", "휴가", "날씨", "비 와",
        "메뉴", "번역", "와이파이", "비밀번호", "다듬", "며칠", "팁",
    ),
}  # fmt: skip

# 이 단어 하나만으로는 어느 영역의 무엇을 묻는지 알 수 없다(대상이 없는 질문).
_VAGUE = frozenset({"일정", "이슈", "진행 상황", "미팅", "연락처"})
_MULTI_PART = ("같이", "함께", "둘 다", "각각", "동시에", "대비", "그리고")


def _mentions(query: str, hint: str) -> bool:
    if hint.isascii():  # 영문 약어는 경계 매칭("CR" 은 "CRM" 에 걸리지 않는다)
        return re.search(rf"(?<![0-9a-z]){re.escape(hint.casefold())}(?![0-9a-z])", query) is not None
    return hint in query


def _judge(req: FakeRequest) -> str:
    query = req.last_user.casefold()
    hits = {d: [h for h in hints if _mentions(query, h)] for d, hints in _JUDGE_HINTS.items()}
    delivery, sales = len(hits[Domain.DELIVERY]), len(hits[Domain.SALES])
    multi_part = any(cue in query for cue in _MULTI_PART)
    yes: set[Domain] = set()
    if (delivery >= 2 and sales >= 2) or (multi_part and delivery and sales):
        yes |= {Domain.DELIVERY, Domain.SALES}
    elif delivery != sales:  # 한쪽이 더 많이 걸리면 그쪽만. 같으면 모호하다고 본다
        domain = Domain.DELIVERY if delivery > sales else Domain.SALES
        if not (len(hits[domain]) == 1 and hits[domain][0] in _VAGUE):
            yes.add(domain)
    if hits[Domain.GENERAL]:
        yes.add(Domain.GENERAL)
    verdicts = []
    for domain in Domain:
        if domain in yes:
            quoted = ", ".join(f"'{h}'" for h in hits[domain][:3])
            reason = f"질문의 {quoted} 표현은 {_LABELS[domain]} 영역의 내용이다."
            verdicts.append({"domain": domain.value, "answer": "YES", "reason": reason})
        else:
            verdicts.append({"domain": domain.value, "answer": "NO", "reason": "해당 영역의 표현이 없다."})
    return json.dumps({"verdicts": verdicts}, ensure_ascii=False)


_DEICTIC_REPLACE = (
    "그 프로젝트",
    "그 고객사",
    "그 건",
    "그거",
    "그건",
    "그게",
    "그걸",
    "거기",
    "그쪽",
    "저거",
)
_DEICTIC_PREFIX = ("이어서", "그럼", "그러면")
_REQUEST_TAIL = re.compile(
    r"\s*(?:을|를)?\s*(?:알려\s*줘|알려\s*주세요|보여\s*줘|정리해\s*줘|요약해\s*줘|찾아\s*줘|어때|뭐야|있어)?[\s?.!]*$"
)
_SHORT_QUERY_CHARS = 8


def _core(question: str) -> str:
    return _REQUEST_TAIL.sub("", question).strip()


def _contextualize(req: FakeRequest) -> str:
    text = req.last_user
    current = text.rsplit("현재 질문:", 1)[-1].strip()
    previous = [
        line.removeprefix("사용자:").strip() for line in text.splitlines() if line.startswith("사용자:")
    ]
    if not previous:
        return current
    core = _core(previous[-1])
    for phrase in _DEICTIC_REPLACE:
        if phrase in current:
            return current.replace(phrase, core, 1)
    if any(current.startswith(p) for p in _DEICTIC_PREFIX) or len(current) <= _SHORT_QUERY_CHARS:
        return f"{core} {current}"
    return current


def _hitl_suggest(req: FakeRequest) -> str:
    units = re.findall(r"\{unit:([^}]+)\}", req.last_user)
    suggestions = []
    if units:
        suggestions.append(f"{{unit:{units[0]}}} 일정 지연 현황 알려줘")
        suggestions.append(f"{{unit:{units[-1]}}} 최근 장애 보고와 조치 결과 정리해줘")
    suggestions.append("담당 고객사의 영업 단계와 담당 KAM 알려줘")
    if not units:
        suggestions.append("이 챗봇 사용법 알려줘")
    return json.dumps({"suggestions": suggestions}, ensure_ascii=False)


_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
_MARKER = re.compile(r"\[\d+\]\(cite:[^)\s]+\)")


def _summary(req: FakeRequest) -> str:
    lines = ["두 영역의 답변을 함께 보면 다음과 같습니다."]
    for block in req.last_user.split("### ")[1:]:
        header, _, body = block.partition("\n")
        first = _SENTENCE_END.split(_MARKER.sub("", body).strip(), maxsplit=1)[0].strip()
        if first:
            lines.append(f"- {header.removesuffix(' 답변').strip()}: {first}")
    return "\n".join(lines)


_FOLLOW_UPS = {
    Domain.DELIVERY: (
        "다음 단계 검수 일정은 언제야?",
        "최근 장애 보고의 재발 방지 대책은?",
        "진행 중인 CR 승인 현황 알려줘",
    ),
    Domain.SALES: (
        "다음 영업 미팅에서 확인할 쟁점은 뭐야?",
        "경쟁사 대비 우리 제안의 차별점은?",
        "수정 견적 회신 일정 알려줘",
    ),
    Domain.GENERAL: (
        "답변 근거는 어떻게 확인해?",
        "답변 말투를 바꿀 수 있어?",
        "답변을 다시 받으려면 어떻게 해?",
    ),
}


def _recommend(req: FakeRequest) -> str:
    match = re.search(r"^영역:\s*(.*)$", req.last_user, re.MULTILINE)
    names = [n.strip() for n in (match.group(1) if match else "").split(",") if n.strip()]
    domains = [Domain(n) for n in names if n in Domain] or [Domain.GENERAL]
    pools = [_FOLLOW_UPS[d] for d in domains]
    questions = [q for row in zip(*pools, strict=True) for q in row][:3]
    return json.dumps({"questions": questions}, ensure_ascii=False)


_EVIDENCE = re.compile(r"^\[(\d+)\] \(([^)]+)\) (.*)$", re.MULTILINE)


def _general_answer(req: FakeRequest) -> str:
    evidence = _EVIDENCE.findall(req.last_user)
    if not evidence:
        return "관련 안내를 찾지 못했습니다. IT 헬프데스크에 문의해 주세요."
    n, ref_id, text = evidence[0]
    if ref_id.startswith("web:"):
        return (
            "사내 안내 자료에서는 답을 찾지 못해 웹 검색 결과를 참고했습니다. 다만 데모 환경이라 "
            f"실제 웹 검색은 하지 않았고, 고정된 예시 결과만 보여 드립니다. [{n}](cite:{ref_id})"
        )
    title, _, answer = text.partition(" — ")
    parts = [f"{answer.strip() or title.strip()} [{n}](cite:{ref_id})"]
    if len(evidence) > 1:
        n2, ref2, text2 = evidence[1]
        parts.append(f"함께 보면 좋은 안내: {text2.partition(' — ')[0].strip()} [{n2}](cite:{ref2})")
    return "\n\n".join(parts)


def fake_handlers() -> dict[str, FakeHandler]:
    return {
        "route.judge": _judge,
        "route.contextualize": _contextualize,
        "hitl.suggest": _hitl_suggest,
        "summary": _summary,
        "recommend": _recommend,
        "general.answer": _general_answer,
    }
