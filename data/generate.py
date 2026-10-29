"""가상 회사 "누리솔루션" 의 합성 데이터 생성기.

모든 회사·인물·프로젝트는 지어낸 것이다. 전화번호는 `010-0000-xxxx`, 메일은 예약
도메인(`.example`)만 쓴다. 시드가 고정이라 다시 돌려도 같은 파일이 나온다.

    uv run python data/generate.py
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parent
SEED = 20261029
rng = random.Random(SEED)

COMPANY_DOMAIN = "nuri-solution.example"

# ── 인물 ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Employee:
    id: str
    name: str
    team: str
    title: str


EMPLOYEES = [
    Employee("u-01", "김서진", "영업1팀", "과장"),
    Employee("u-02", "이도윤", "사업수행1팀", "PM"),
    Employee("u-03", "박서연", "경영지원팀", "사원"),
    Employee("u-04", "최지훈", "영업2팀", "팀장"),
    Employee("e-05", "정유진", "사업수행2팀", "PM"),
    Employee("e-06", "윤태오", "사업수행1팀", "개발리더"),
    Employee("e-07", "강민재", "사업수행2팀", "개발리더"),
    Employee("e-08", "서지안", "영업1팀", "대리"),
    Employee("e-09", "임도현", "영업2팀", "과장"),
    Employee("e-10", "조하린", "품질관리팀", "QA"),
]
EMP = {e.id: e for e in EMPLOYEES}

# 데모 로그인 사용자 — 권한 범위가 서로 다르다.
DEMO_USERS = [
    {
        "sub": "u-01",
        "name": "김서진",
        "team": "영업1팀",
        "title": "과장",
        "description": "유통·물류 고객 담당 영업. 연락처 열람 권한, 수행 프로젝트 1개 열람",
        "project_ids": ["P-101"],
        "sales_units": ["S1"],
        "can_read_contacts": True,
    },
    {
        "sub": "u-02",
        "name": "이도윤",
        "team": "사업수행1팀",
        "title": "PM",
        "description": "유통·물류 프로젝트 3개 수행 PM. 영업 비공개 자료·연락처 권한 없음",
        "project_ids": ["P-101", "P-102", "P-103"],
        "sales_units": [],
        "can_read_contacts": False,
    },
    {
        "sub": "u-03",
        "name": "박서연",
        "team": "경영지원팀",
        "title": "사원",
        "description": "권한이 거의 없는 사용자. 공개 영업 정보와 사용 안내만 볼 수 있음",
        "project_ids": [],
        "sales_units": [],
        "can_read_contacts": False,
    },
    {
        "sub": "u-04",
        "name": "최지훈",
        "team": "영업2팀",
        "title": "팀장",
        "description": "제조·금융 고객 영업 팀장. 연락처 열람, 수행 프로젝트 3개 열람",
        "project_ids": ["P-104", "P-105", "P-106"],
        "sales_units": ["S2"],
        "can_read_contacts": True,
    },
]

CUSTOMER_PEOPLE = [
    "송현우", "권나연", "황지호", "안다은", "유건우", "장예린", "백승훈", "노은채",
    "하준서", "구민서", "표지성", "남다인", "엄태윤", "변수현", "도하은", "탁재민",
    "여시우", "봉채윤", "우지환", "석예나", "제갈현", "명서율", "피승아", "진도겸",
]  # fmt: skip

COMPETITORS = ["에이펙스소프트", "블루웨이브시스템", "코어브릿지"]

# ── 고객사 · 프로젝트 ────────────────────────────────────────────────────

ACCOUNTS = [
    # id, 이름, 업종, 영업단위, 담당 KAM, 상태, 주력 제안
    ("A-01", "한빛유통", "유통", "S1", "u-01", "수주", "누리 WMS"),
    ("A-02", "새솔물류", "물류", "S1", "e-08", "수주", "누리 RouteOpt"),
    ("A-03", "다온마트", "유통", "S1", "u-01", "수주", "누리 DataHub"),
    ("A-04", "푸른항운", "물류", "S1", "e-08", "협상", "누리 WMS"),
    ("A-05", "대성정밀", "제조", "S2", "u-04", "수주", "누리 MES"),
    ("A-06", "미래캐피탈", "금융", "S2", "e-09", "수주", "누리 DataHub"),
    ("A-07", "온누리제약", "제약", "S2", "u-04", "수주", "누리 QMS"),
    ("A-08", "세움모터스", "제조", "S2", "e-09", "제안", "누리 MES"),
]

PROJECTS = [
    # id, 고객사, 이름, PM, 시작, 종료, 모듈들
    ("P-101", "A-01", "한빛유통 물류센터 WMS 고도화", "u-02", "2026-02-02", "2026-09-30",
     ["입고", "출고", "재고 실사", "피킹 최적화"]),
    ("P-102", "A-02", "새솔물류 배차 최적화 시스템 구축", "u-02", "2026-03-03", "2026-11-28",
     ["배차 엔진", "차량 관제", "운임 정산"]),
    ("P-103", "A-03", "다온마트 통합 판매 데이터 플랫폼", "u-02", "2026-04-01", "2026-12-18",
     ["POS 수집", "데이터 레이크", "매출 대시보드"]),
    ("P-104", "A-05", "대성정밀 MES 구축", "e-05", "2026-01-12", "2026-10-30",
     ["작업지시", "설비 모니터링", "품질 검사", "자재 추적"]),
    ("P-105", "A-06", "미래캐피탈 여신 심사 자동화", "e-05", "2026-03-16", "2026-12-11",
     ["심사 규칙 엔진", "서류 OCR", "심사 대시보드"]),
    ("P-106", "A-07", "온누리제약 품질문서 관리시스템 전환", "e-05", "2026-05-04", "2027-01-29",
     ["문서 결재", "교육 이력", "일탈·CAPA"]),
]  # fmt: skip
ACCOUNT_NAME = {a[0]: a[1] for a in ACCOUNTS}

# ── 유틸 ──────────────────────────────────────────────────────────────

_phone_seq = iter(range(1000, 9999))


def fake_phone() -> str:
    return f"010-0000-{next(_phone_seq):04d}"


def rand_date(start: str, end: str) -> date:
    a, b = date.fromisoformat(start), date.fromisoformat(end)
    return a + timedelta(days=rng.randrange((b - a).days + 1))


def pick(seq: list[Any]) -> Any:
    return rng.choice(seq)


def josa(word: str, pair: str) -> str:
    """받침 유무에 맞춰 조사를 붙인다. pair 예: '은/는', '이/가'."""
    last = word[-1]
    has_final = "가" <= last <= "힣" and (ord(last) - 0xAC00) % 28 != 0
    with_final, without_final = pair.split("/")
    return word + (with_final if has_final else without_final)


def write(rel: str, rows: Any) -> None:
    path = ROOT / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"  {rel}: {len(rows)}")


def signature(name: str, team: str, phone: str, email: str) -> str:
    return f"\n\n{name} 드림 | {team} | {phone} | {email}"


# ── 영업 데이터 ──────────────────────────────────────────────────────────


def build_contacts() -> list[dict[str, Any]]:
    rows = []
    people = iter(CUSTOMER_PEOPLE)
    for acc_id, acc_name, *_ in ACCOUNTS:
        for role, title, dept in (
            ("의사결정권자", "상무", "경영기획실"),
            ("실무 책임자", "부장", "IT전략팀"),
            ("실무 담당자", "대리", pick(["물류운영팀", "생산기술팀", "디지털혁신팀", "구매팀"])),
        ):
            name = next(people)
            rows.append(
                {
                    "id": f"C-{len(rows) + 1:02d}",
                    "account_id": acc_id,
                    "name": name,
                    "title": title,
                    "department": dept,
                    "role": role,
                    "phone": fake_phone(),
                    "email": f"contact{len(rows) + 1:02d}@{acc_id.lower()}.example",
                    "note": f"{acc_name} {dept} {title}. {pick(['기술 검토를 주도', '예산 승인권 보유', '현업 요구사항 취합 담당'])}.",
                }
            )
    return rows


MEETING_TYPES = {
    "니즈 파악": [
        "{acc}의 현행 시스템은 도입한 지 7년이 넘어 {pain} 문제가 반복되고 있다고 함.",
        "{contact} {ctitle_eun} 올해 안에 {goal}을 목표로 하며, 예산은 하반기 편성 예정이라고 언급.",
        "후속으로 현행 업무 흐름 자료를 받아 {product} 적용 범위를 정리하기로 함.",
    ],
    "제안 설명회": [
        "{product} 제안 설명회를 진행함. 참석자는 {contact} {ctitle} 외 현업 4명.",
        "질의는 주로 {pain} 개선 효과와 기존 시스템과의 연계 방식에 집중됨. 데모에서 {feature} 기능 반응이 좋았음.",
        "경쟁사 {competitor}도 제안서를 낸 것으로 파악됨. 차별점으로 구축 기간 단축을 강조하기로 함.",
    ],
    "가격 협상": [
        "{contact} {ctitle_i} 1차 견적 {amount}억 원 대비 10% 인하를 요청함.",
        "라이선스는 유지하고 구축 범위 중 {feature} 고도화를 2단계로 미루는 대안을 제시함.",
        "다음 주까지 수정 견적과 단계별 일정을 회신하기로 함. 결재는 {month}월 경영회의 예정.",
    ],
    "기술 검토": [
        "고객 IT전략팀과 {product} 아키텍처를 검토함. 클라우드 전환 계획과의 정합성이 쟁점.",
        "기존 ERP 연계는 인터페이스 12본으로 추정. 보안 심의 항목으로 접근 통제와 로그 보관 기간 확인 요청.",
        "검토 결과를 정리한 기술 답변서를 {month}월 둘째 주까지 제출하기로 함.",
    ],
    "계약 조건 협의": [
        "계약 형태는 구축 + 2년 유지보수로 합의 방향. 하자보수 기간은 12개월 제안.",
        "{contact} {ctitle_eun} 지체상금 조항 완화를 요청했고, 법무 검토 후 회신하기로 함.",
        "착수 목표일은 {month}월 첫째 주. 착수 전까지 PM 배정을 확정해야 함.",
    ],
}

PAINS = ["재고 불일치", "출고 지연", "수작업 정산", "데이터 중복 입력", "설비 이력 단절", "문서 결재 지연"]
GOALS = ["처리 시간 30% 단축", "재고 정확도 99% 달성", "월 마감 이틀 단축", "현업 수작업 절반 축소"]
FEATURES = ["실시간 대시보드", "자동 배차", "모바일 검수", "이상 탐지 알림", "전자 결재 연동"]


def build_meetings(contacts: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    meetings, attachments = [], []
    for acc_id, acc_name, _ind, unit, kam, status, product in ACCOUNTS:
        acc_contacts = [c for c in contacts if c["account_id"] == acc_id]
        stages = list(MEETING_TYPES)
        if status in ("제안", "협상"):
            stages = stages[: 4 if status == "협상" else 2]
        for n, kind in enumerate(stages):
            contact = acc_contacts[min(n, 2)]
            when = date(2025, 10, 6) + timedelta(days=21 * n + rng.randrange(7))
            values = {
                "acc": acc_name,
                "contact": contact["name"],
                "ctitle": contact["title"],
                "ctitle_eun": josa(contact["title"], "은/는"),
                "ctitle_i": josa(contact["title"], "이/가"),
                "pain": pick(PAINS),
                "goal": pick(GOALS),
                "product": product,
                "feature": pick(FEATURES),
                "competitor": pick(COMPETITORS),
                "amount": pick([6, 8, 12, 15, 18]),
                "month": (when.month % 12) + 1,
            }
            chunks = [t.format(**values) for t in MEETING_TYPES[kind]]
            if n == 0:
                chunks[-1] += f" 고객 담당 연락처: {contact['name']} {contact['phone']}"
            meeting_id = f"M-{len(meetings) + 1:03d}"
            meetings.append(
                {
                    "id": meeting_id,
                    "account_id": acc_id,
                    "unit": unit,
                    "confidential": kind in ("가격 협상", "계약 조건 협의"),
                    "date": when.isoformat(),
                    "title": f"{acc_name} {kind}",
                    "kind": kind,
                    "owner": EMP[kam].name,
                    "attendees": [EMP[kam].name, contact["name"]],
                    "chunks": [{"seq": i + 1, "text": c} for i, c in enumerate(chunks)],
                }
            )
            if kind in ("제안 설명회", "가격 협상"):
                attachments.append(
                    {
                        "id": f"MA-{len(attachments) + 1:02d}",
                        "meeting_id": meeting_id,
                        "filename": f"{acc_name}_{'제안요약' if kind == '제안 설명회' else '수정견적'}.pdf",
                        "text": (
                            f"{acc_name} 대상 {product} {'제안 요약' if kind == '제안 설명회' else '수정 견적'}. "
                            f"구축 범위: {', '.join(rng.sample(FEATURES, 3))}. "
                            f"총액 {values['amount']}억 원(라이선스 40%, 구축 45%, 유지보수 15%)."
                        ),
                    }
                )
    return meetings, attachments


def build_accounts() -> list[dict[str, Any]]:
    rows = []
    for acc_id, name, industry, unit, kam, status, product in ACCOUNTS:
        rows.append(
            {
                "id": acc_id,
                "name": name,
                "industry": industry,
                "unit": unit,
                "kam": EMP[kam].name,
                "kam_id": kam,
                "status": status,
                "main_offer": product,
                "employees": pick([320, 850, 1200, 2400, 5100]),
                "summary": (
                    f"{industry} 업종 고객. 주력 제안은 {product}. 현재 영업 단계는 '{status}'. "
                    f"주요 관심사는 {pick(PAINS)} 해소."
                ),
            }
        )
    return rows


# ── 수행 데이터 ──────────────────────────────────────────────────────────

MAIL_TOPICS = {
    "일정 지연": (
        "[{pname}] {module} 일정 지연 공유",
        "{module} 개발이 계획 대비 {days}일 지연되고 있습니다. 원인은 {cause}입니다. "
        "만회를 위해 다음 주부터 개발 인력 1명을 추가 투입하고, 통합 테스트 시작일을 {d1} 기준으로 조정하려 합니다. "
        "고객사 검토 의견 부탁드립니다.",
    ),
    "요구사항 변경": (
        "[{pname}] {module} 요구사항 변경 요청(CR-{cr}) 검토",
        "고객 현업에서 {module} 화면에 {feature} 기능 추가를 요청했습니다. 영향도 분석 결과 개발 공수는 "
        "{md}MD, 일정 영향은 {days}일로 추정됩니다. 변경 요청서(CR-{cr})를 첨부하니 승인 여부 회신 바랍니다.",
    ),
    "테스트 결과": (
        "[{pname}] {phase} 결과 보고",
        "{phase}에서 시나리오 {total}건 중 {passed}건이 통과했습니다. 결함 {defects}건 중 치명 결함은 "
        "{critical}건이며 {module} 영역에 집중되어 있습니다. 재테스트는 {d1}에 진행합니다.",
    ),
    "장애 보고": (
        "[{pname}] 운영 장애 경위 보고 — {module}",
        "{d0} 오전 {module}에서 응답 지연 장애가 약 {mins}분간 발생했습니다. 원인은 {cause}이며 "
        "임시 조치 후 정상화되었습니다. 재발 방지를 위해 모니터링 임계값을 조정하고 근본 원인 분석을 진행합니다.",
    ),
    "인력 투입": (
        "[{pname}] 인력 투입 계획 변경 안내",
        "{module} 구간 일정 만회를 위해 {d1}부터 개발자 {heads}명이 추가 투입됩니다. 투입 기간은 {weeks}주이며 "
        "비용은 예비비 범위 안에서 처리합니다.",
    ),
    "검수 일정": (
        "[{pname}] 단계 검수 일정 협의",
        "{module} 단계 검수를 {d1}에 진행하고자 합니다. 검수 기준은 요구사항 추적표 기준 완료율 100%와 "
        "치명 결함 0건입니다. 고객 측 검수 참석자 확정 부탁드립니다.",
    ),
    "비용 정산": (
        "[{pname}] {month}월 기성 정산 자료 송부",
        "{month}월 기성 청구 금액은 {amount}천만 원입니다. 공정률 {progress}% 기준으로 산정했으며 "
        "증빙 자료를 첨부합니다. 확인 후 승인 부탁드립니다.",
    ),
}
CAUSES = ["외부 인터페이스 사양 변경", "테스트 데이터 준비 지연", "DB 인덱스 누락", "배치 작업 시간 중복",
          "고객 측 의사결정 지연", "네트워크 방화벽 정책 미반영"]  # fmt: skip
PHASES = ["단위 테스트", "통합 테스트(SIT)", "사용자 인수 테스트(UAT)"]

NOTE_KINDS = {
    "주간보고": (
        "{pname} 주간보고 ({d1})",
        "전체 공정률 {progress}%(계획 대비 {gap:+d}%p). 이번 주 완료: {module} 화면 개발, 인터페이스 2본 연동. "
        "이슈: {cause}. 다음 주 계획: {module2} 개발 착수, {phase} 준비.",
    ),
    "이슈 메모": (
        "{pname} 이슈 메모 — {module}",
        "{module}에서 {cause} 이슈가 발생. 영향 범위는 {module2}까지. 조치 담당은 {owner}, 목표 해결일 {d1}.",
    ),
    "회의록": (
        "{pname} {module} 요구사항 회의",
        "고객 현업과 {module} 요구사항을 검토함. {feature} 기능은 이번 범위에 포함하기로 합의. "
        "변경 요청(CR-{cr})으로 처리하며 일정 영향은 {days}일 이내로 관리.",
    ),
}


def build_delivery() -> tuple[list[dict[str, Any]], ...]:
    projects, mails, notes, files = [], [], [], []
    for pid, acc_id, pname, pm, start, end, modules in PROJECTS:
        projects.append(
            {
                "id": pid,
                "account_id": acc_id,
                "account_name": ACCOUNT_NAME[acc_id],
                "name": pname,
                "pm": EMP[pm].name,
                "start": start,
                "end": end,
                "modules": modules,
                "status": "진행",
            }
        )
        lead = "e-06" if pm == "u-02" else "e-07"
        for _ in range(20):
            topic = pick(list(MAIL_TOPICS))
            subject_t, body_t = MAIL_TOPICS[topic]
            sent = rand_date(start, "2026-10-24")
            values = {
                "pname": pname,
                "module": pick(modules),
                "days": pick([3, 5, 7, 10, 14]),
                "cause": pick(CAUSES),
                # 보고 대상(장애 등)은 발송일 이전, 예정 일정은 이후. d0 는 난수를 쓰지 않아 다른 값이 그대로다.
                "d0": (sent - timedelta(days=2)).isoformat(),
                "d1": (sent + timedelta(days=pick([3, 7, 10]))).isoformat(),
                "feature": pick(FEATURES),
                "cr": rng.randrange(10, 99),
                "md": pick([5, 8, 12, 20]),
                "phase": pick(PHASES),
                "total": (total := pick([80, 120, 150])),
                "passed": total - (fail := pick([4, 9, 15, 22])),
                "defects": fail,
                "critical": pick([0, 1, 2]),
                "mins": pick([12, 25, 40]),
                "heads": pick([1, 2, 3]),
                "weeks": pick([2, 4, 6]),
                "month": sent.month,
                "amount": pick([18, 24, 31, 42]),
                "progress": pick([35, 48, 62, 77, 85]),
            }
            sender = EMP[pick([pm, lead])]
            body = body_t.format(**values)
            if rng.random() < 0.4:
                body += signature(sender.name, sender.team, fake_phone(), f"{sender.id}@{COMPANY_DOMAIN}")
            mail_id = f"ML-{len(mails) + 1:04d}"
            mails.append(
                {
                    "id": mail_id,
                    "project_id": pid,
                    "date": sent.isoformat(),
                    "topic": topic,
                    "sender": sender.name,
                    "subject": subject_t.format(**values),
                    "body": body,
                }
            )
            if topic in ("요구사항 변경", "테스트 결과", "비용 정산"):
                kind = {
                    "요구사항 변경": "변경요청서",
                    "테스트 결과": "테스트결과서",
                    "비용 정산": "기성내역서",
                }[topic]
                files.append(
                    {
                        "id": f"F-{len(files) + 1:03d}",
                        "parent_id": mail_id,
                        "parent_type": "mail",
                        "project_id": pid,
                        "filename": f"{pid}_{kind}_{sent:%m%d}.xlsx",
                        "text": f"{kind}: {mails[-1]['subject']}. {body.split('.')[0]}.",
                    }
                )
        for _ in range(10):
            kind = pick(list(NOTE_KINDS))
            title_t, body_t = NOTE_KINDS[kind]
            when = rand_date(start, "2026-10-24")
            two = rng.sample(modules, 2)
            values = {
                "pname": pname,
                "d1": when.isoformat(),
                "progress": pick([35, 48, 62, 77, 85]),
                "gap": pick([-6, -3, 0, 2]),
                "module": two[0],
                "module2": two[1],
                "cause": pick(CAUSES),
                "phase": pick(PHASES),
                "owner": EMP[lead].name,
                "feature": pick(FEATURES),
                "cr": rng.randrange(10, 99),
                "days": pick([3, 5, 7]),
            }
            notes.append(
                {
                    "id": f"N-{len(notes) + 1:03d}",
                    "project_id": pid,
                    "date": when.isoformat(),
                    "kind": kind,
                    "author": EMP[pick([pm, lead])].name,
                    "title": title_t.format(**values),
                    "body": body_t.format(**values),
                }
            )
    return projects, mails, notes, files


GLOSSARY = [
    {"term": "WMS", "aliases": ["창고관리시스템"], "description": "입고·출고·재고를 관리하는 창고 관리 시스템"},
    {"term": "MES", "aliases": ["제조실행시스템"], "description": "생산 현장의 작업지시·실적·품질을 관리하는 시스템"},
    {"term": "QMS", "aliases": ["품질관리시스템"], "description": "품질 문서·일탈·CAPA 를 관리하는 시스템"},
    {"term": "CR", "aliases": ["변경요청", "변경 요청서"], "description": "합의된 범위를 바꾸기 위한 공식 요청"},
    {"term": "UAT", "aliases": ["사용자 인수 테스트", "인수테스트"], "description": "고객 현업이 수행하는 최종 테스트"},
    {"term": "SIT", "aliases": ["통합 테스트", "통합테스트"], "description": "시스템 간 연계를 검증하는 테스트"},
    {"term": "기성", "aliases": ["기성고", "기성 청구"], "description": "공정률에 따라 대금을 나눠 청구하는 것"},
    {"term": "검수", "aliases": ["인수 검사", "단계 검수"], "description": "산출물이 기준을 충족하는지 고객이 확인하는 절차"},
    {"term": "킥오프", "aliases": ["착수 회의", "착수보고"], "description": "프로젝트 시작 시 범위·일정을 공유하는 회의"},
    {"term": "컷오버", "aliases": ["운영 전환", "오픈 전환"], "description": "신규 시스템으로 운영을 넘기는 작업"},
    {"term": "WBS", "aliases": ["작업분류체계"], "description": "프로젝트 작업을 계층으로 나눈 일정 계획"},
    {"term": "MD", "aliases": ["맨데이", "인일"], "description": "1인이 하루 일하는 작업량 단위"},
    {"term": "CAPA", "aliases": ["시정예방조치"], "description": "품질 문제의 시정·예방 조치"},
    {"term": "PO", "aliases": ["발주서", "구매주문"], "description": "구매 발주 문서",
     "ambiguous_with": "프로덕트 오너(Product Owner)"},
    {"term": "PM", "aliases": ["프로젝트 관리자"], "description": "프로젝트 수행 책임자",
     "ambiguous_with": "오후(post meridiem)"},
]  # fmt: skip

FAQ = [
    ("대화 기록은 어디에서 볼 수 있나요?", "왼쪽 대화 목록에서 날짜별로 지난 대화를 볼 수 있습니다. 대화를 누르면 이어서 질문할 수 있습니다."),
    ("답변 근거는 어떻게 확인하나요?", "답변 본문의 [1], [2] 같은 번호를 누르면 오른쪽 참조 패널에서 해당 자료가 강조됩니다."),
    ("답변을 다시 받고 싶어요.", "답변 아래의 '다시 생성'을 누르면 같은 질문으로 새 답변을 받습니다. 이전 답변은 기록에서 숨겨집니다."),
    ("답변 생성을 멈출 수 있나요?", "답변이 나오는 동안 입력창 옆의 '중지' 버튼을 누르면 생성을 멈춥니다."),
    ("왜 질문을 다시 물어보나요?", "질문만으로는 영업 자료와 수행 자료 중 어디를 찾아야 할지 정하기 어려울 때 범위를 확인합니다. 제안된 질문을 누르거나 영역 버튼을 고르면 됩니다."),
    ("볼 수 있는 자료 범위는 어떻게 정해지나요?", "로그인한 사용자의 권한에 따라 정해집니다. 수행 자료는 배정된 프로젝트만, 비공개 영업 회의록은 담당 영업 단위만 볼 수 있습니다."),
    ("고객 담당자 연락처가 가려져서 나와요.", "연락처 원문은 열람 권한이 있는 사용자에게만 보입니다. 권한이 필요하면 소속 팀장에게 요청하세요."),
    ("답변 말투를 바꿀 수 있나요?", "설정의 '답변 지침'에 원하는 말투나 형식을 적으면 이후 답변에 반영됩니다."),
    ("영업 자료와 수행 자료를 한 번에 물어볼 수 있나요?", "네. 두 영역에 걸친 질문이면 두 에이전트가 함께 답하고 마지막에 요약을 붙입니다."),
    ("특정 프로젝트만 대상으로 물어보고 싶어요.", "입력창 위의 프로젝트 선택에서 범위를 고르면 그 프로젝트 자료에서만 찾습니다."),
    ("출장비 정산은 어떻게 하나요?", "출장 종료 후 7일 이내에 경비 시스템에서 영수증을 첨부해 신청합니다. 팀장 승인 후 다음 달 급여일에 지급됩니다."),
    ("사내 보안 교육은 언제 받아야 하나요?", "모든 임직원은 매년 상반기 안에 온라인 보안 교육을 이수해야 합니다."),
    ("고객 자료를 외부로 보내도 되나요?", "고객 자료는 사내 승인 절차 없이 외부로 보낼 수 없습니다. 반출이 필요하면 보안 담당 부서에 반출 신청을 해야 합니다."),
    ("노트북을 분실했어요.", "즉시 IT 헬프데스크에 신고하세요. 원격 잠금과 계정 비밀번호 변경을 진행합니다."),
]  # fmt: skip


def main() -> None:
    print("누리솔루션 합성 데이터 생성")
    contacts = build_contacts()
    meetings, meeting_files = build_meetings(contacts)
    projects, mails, notes, files = build_delivery()
    write("users.json", DEMO_USERS)
    write("sales/accounts.json", build_accounts())
    write("sales/contacts.json", contacts)
    write("sales/meetings.json", meetings)
    write("sales/meeting_attachments.json", meeting_files)
    write("delivery/projects.json", projects)
    write("delivery/mails.json", mails)
    write("delivery/notes.json", notes)
    write("delivery/attachments.json", files)
    write("delivery/glossary.json", GLOSSARY)
    write("general/faq.json", [{"id": f"FAQ-{i + 1:02d}", "q": q, "a": a} for i, (q, a) in enumerate(FAQ)])


if __name__ == "__main__":
    main()
