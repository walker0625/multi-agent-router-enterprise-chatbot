"""검색 결과 → 도구 결과(JSON)와 근거 카드.

모델에게 주는 JSON 과 화면의 근거 카드를 같은 결과 객체에서 한 번에 만든다. 따로 만들면
카드에는 있는데 모델은 못 본 근거, 모델은 인용했는데 카드가 없는 근거가 생긴다. 도구 결과의
각 항목에 카드 id(`cite_id`)를 넣어 모델이 그대로 인용하게 한다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from chatcore.events import Reference
from sales_agent.retrieval import AccountItem, AttachmentItem, ContactItem, Intent, Item, MeetingItem

SNIPPET_CHARS = 160


@dataclass(frozen=True)
class Presented:
    results: dict[str, list[dict[str, Any]]]  # intent → 도구 결과 항목
    references: list[Reference]


def present(results: Mapping[Intent, Sequence[Item]]) -> Presented:
    out: dict[str, list[dict[str, Any]]] = {}
    references: list[Reference] = []
    for intent, items in results.items():
        entries = out.setdefault(intent, [])
        for item in items:
            entry, cards = _present(item)
            entries.append(entry)
            references.extend(cards)
    return Presented(out, references)


def _present(item: Item) -> tuple[dict[str, Any], list[Reference]]:
    match item:
        case AccountItem():
            return _account(item)
        case ContactItem():
            return _contact(item)
        case MeetingItem():
            return _meeting(item)


def _score(value: float) -> float:
    return round(value, 4)


def _account(a: AccountItem) -> tuple[dict[str, Any], list[Reference]]:
    cite = f"account:{a.id}"
    entry = {
        "cite_id": cite,
        "name": a.name,
        "industry": a.industry,
        "status": a.status,
        "kam": a.kam,
        "main_offer": a.main_offer,
        "summary": a.summary,
    }
    card = Reference(
        id=cite,
        source="account",
        title=a.name,
        snippet=a.summary[:SNIPPET_CHARS],
        score=_score(a.score),
        meta={"industry": a.industry, "status": a.status, "kam": a.kam, "unit": a.unit},
    )
    return entry, [card]


def _contact(c: ContactItem) -> tuple[dict[str, Any], list[Reference]]:
    cite = f"contact:{c.id}"
    entry = {
        "cite_id": cite,
        "account": c.account_name,
        "name": c.name,
        "title": c.title,
        "department": c.department,
        "role": c.role,
        "note": c.note,
        "phone": c.phone,
        "email": c.email,
    }
    card = Reference(
        id=cite,
        source="contact",
        title=f"{c.name} {c.title} ({c.account_name} {c.department})",
        snippet=f"{c.role}. {c.note}"[:SNIPPET_CHARS],
        score=_score(c.score),
        meta={"account_id": c.account_id, "role": c.role, "phone": c.phone, "email": c.email},
    )
    return entry, [card]


def _meeting(m: MeetingItem) -> tuple[dict[str, Any], list[Reference]]:
    cite = f"meeting:{m.id}"
    attachments = [_attachment(a) for a in m.attachments]
    entry = {
        "cite_id": cite,
        "title": m.title,
        "date": m.date,
        "kind": m.kind,
        "owner": m.owner,
        "confidential": m.confidential,
        "excerpts": m.excerpts,
        "attachments": [entry for entry, _ in attachments],
    }
    card = Reference(
        id=cite,
        source="meeting",
        title=m.title,
        snippet=" ".join(m.excerpts)[:SNIPPET_CHARS],
        date=m.date,
        score=_score(m.score),
        meta={
            "account_id": m.account_id,
            "kind": m.kind,
            "owner": m.owner,
            "confidential": str(m.confidential).lower(),
        },
    )
    return entry, [card, *(card for _, card in attachments)]


def _attachment(a: AttachmentItem) -> tuple[dict[str, Any], Reference]:
    cite = f"attachment:{a.id}"
    entry = {"cite_id": cite, "filename": a.filename, "text": a.text}
    card = Reference(
        id=cite,
        source="attachment",
        title=a.filename,
        snippet=a.text[:SNIPPET_CHARS],
        date=a.date,
        score=_score(a.score),
        meta={"meeting_id": a.meeting_id},
    )
    return entry, card
