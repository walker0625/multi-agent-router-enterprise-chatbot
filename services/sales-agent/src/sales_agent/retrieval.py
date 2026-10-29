"""영업 자료 검색 — 메모이즈한 하이브리드 검색과 intent별 조회.

- 같은 (인덱스, 질의, 필터) 검색은 한 턴 안에서 한 번만 실제로 실행한다. 메모 키에 top_k 를
  넣지 않으려고 항상 같은 깊이(`SEARCH_DEPTH`)로 찾고 호출부가 잘라 쓴다.
- 실제 검색마다 턴 예산의 검색 수를 쓴다. 상한을 넘으면 `BudgetExceeded`.
- 회의록은 청크로 찾은 뒤 회의 단위로 접고(회의마다 점수 상위 2개 청크를 seq 순으로),
  찾은 회의 id 로 첨부를 한 번 더 찾는다. 2단계에도 권한 필터를 다시 건다 — 첨부는 부모
  권한을 물려받았지만, 회의 id 목록만 믿으면 1단계 필터가 바뀌었을 때 첨부가 혼자 열릴 수 있다.
- 결과로 나가는 모든 본문은 여기서 `redact()` 를 한 번 거친다(출처 무관). 검색 경계에서
  한 번 하므로 도구 결과·근거 카드·로그 어디에도 본문 속 연락처 원문이 가지 않는다.
- 고객사 이름은 실제 이름과 대조해 `account_id` 로 바꾼 뒤에만 필터에 넣는다. 못 찾으면
  이름 조건 없이 질의어로만 검색한다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Literal, NamedTuple

from chatcore.filters import Eq, Filter, Range, all_of, in_
from chatcore.llm import Embedder
from chatcore.privacy import FieldCipher, pick, redact
from chatcore.search import Hit, SearchIndex
from sales_agent.context import RequestContext
from sales_agent.indexes import SalesIndexes
from sales_agent.privacy import CONTACT_FIELDS, contact_channels

Intent = Literal["account", "contact", "meeting"]

SEARCH_DEPTH = 30
CHUNKS_PER_MEETING = 2


@dataclass(frozen=True)
class AccountItem:
    id: str
    name: str
    industry: str
    unit: str
    status: str
    kam: str
    main_offer: str
    summary: str
    score: float


@dataclass(frozen=True)
class ContactItem:
    id: str
    account_id: str
    account_name: str
    name: str
    title: str
    department: str
    role: str
    note: str
    phone: str  # 원문 또는 `[비공개]`
    email: str
    score: float


@dataclass(frozen=True)
class AttachmentItem:
    id: str
    meeting_id: str
    filename: str
    date: str
    text: str
    score: float


@dataclass(frozen=True)
class MeetingItem:
    id: str
    account_id: str
    title: str
    date: str
    kind: str
    owner: str
    confidential: bool
    excerpts: list[str]  # seq 순
    score: float
    attachments: list[AttachmentItem] = field(default_factory=list)


Item = AccountItem | ContactItem | MeetingItem


class Fold(NamedTuple):
    meeting_id: str
    score: float  # 청크 점수의 최대값
    chunks: list[Hit]  # 점수 상위 청크를 seq 순으로


def fold_chunks(hits: Sequence[Hit]) -> list[Fold]:
    """청크 적중을 회의 단위로 접어 회의 점수 내림차순으로 돌려준다."""
    groups: dict[str, list[Hit]] = {}
    for hit in hits:
        groups.setdefault(hit.doc.fields["meeting_id"], []).append(hit)
    folds = []
    for meeting_id, group in groups.items():
        best = sorted(group, key=lambda h: h.score, reverse=True)[:CHUNKS_PER_MEETING]
        best.sort(key=lambda h: h.doc.fields["seq"])
        folds.append(Fold(meeting_id, max(h.score for h in group), best))
    folds.sort(key=lambda f: f.score, reverse=True)
    return folds


def resolve_account(name: str, account_names: Mapping[str, str]) -> str | None:
    """이름을 실제 고객사와 대조해 id 로. 정확히 같거나, 실제 이름을 하나만 포함할 때만 인정한다."""

    def norm(text: str) -> str:
        return "".join(text.split()).casefold()

    wanted = norm(name)
    for account_id, actual in account_names.items():
        if norm(actual) == wanted:
            return account_id
    contained = [account_id for account_id, actual in account_names.items() if norm(actual) in wanted]
    return contained[0] if len(contained) == 1 else None


class Retriever:
    """인덱스는 공유하고, 요청별 상태(권한·예산·메모)는 모두 `RequestContext` 로 받는다."""

    def __init__(self, indexes: SalesIndexes, embedder: Embedder, cipher: FieldCipher) -> None:
        self._indexes = indexes
        self._embedder = embedder
        self._cipher = cipher

    def account_id(self, name: str) -> str | None:
        return resolve_account(name, self._indexes.account_names)

    def account_name(self, account_id: str) -> str:
        return self._indexes.account_names[account_id]

    async def find(
        self,
        ctx: RequestContext,
        intent: Intent,
        query: str,
        *,
        account_id: str | None,
        date_from: date | None,
        date_to: date | None,
        top_k: int,
    ) -> list[AccountItem] | list[ContactItem] | list[MeetingItem]:
        account = Eq("account_id", account_id) if account_id else None
        match intent:
            case "account":
                return await self.accounts(ctx, query, account, top_k)
            case "contact":
                return await self.contacts(ctx, query, account, top_k)
            case "meeting":
                dates = Range("date", gte=date_from, lte=date_to) if date_from or date_to else None
                return await self.meetings(ctx, query, all_of(account, dates), top_k)

    async def search(
        self, ctx: RequestContext, index: SearchIndex, query: str, filter: Filter | None
    ) -> list[Hit]:
        key = (index.name, query, filter)
        if key in ctx.memo:
            return ctx.memo[key]
        ctx.budget.spend_search()
        vector = await self._vector(ctx, query)
        hits = await ctx.budget.deadline.run(
            index.search(text=query, vector=vector, filter=filter, top_k=SEARCH_DEPTH)
        )
        ctx.memo[key] = hits
        return hits

    async def _vector(self, ctx: RequestContext, query: str) -> list[float]:
        if query not in ctx.vectors:
            [ctx.vectors[query]] = await ctx.budget.deadline.run(self._embedder.embed([query]))
        return ctx.vectors[query]

    async def accounts(
        self, ctx: RequestContext, query: str, filter: Filter | None, top_k: int
    ) -> list[AccountItem]:
        hits = await self.search(ctx, self._indexes.accounts, query, filter)
        items = []
        for hit in hits[:top_k]:
            f = hit.doc.fields
            items.append(
                AccountItem(
                    id=hit.doc.id,
                    name=f["name"],
                    industry=f["industry"],
                    unit=f["unit"],
                    status=f["status"],
                    kam=f["kam"],
                    main_offer=f["main_offer"],
                    summary=redact(f["summary"]),
                    score=hit.score,
                )
            )
        return items

    async def contacts(
        self, ctx: RequestContext, query: str, filter: Filter | None, top_k: int
    ) -> list[ContactItem]:
        hits = await self.search(ctx, self._indexes.contacts, query, filter)
        items = []
        for hit in hits[:top_k]:
            view = pick(hit.doc.fields, CONTACT_FIELDS)
            phone, email = contact_channels(hit.doc.fields, ctx.principal, self._cipher)
            items.append(
                ContactItem(
                    id=hit.doc.id,
                    account_id=view["account_id"],
                    account_name=view["account_name"],
                    name=view["name"],
                    title=view["title"],
                    department=view["department"],
                    role=view["role"],
                    note=redact(view["note"]),
                    phone=phone,
                    email=email,
                    score=hit.score,
                )
            )
        return items

    async def meetings(
        self, ctx: RequestContext, query: str, filter: Filter | None, top_k: int
    ) -> list[MeetingItem]:
        hits = await self.search(ctx, self._indexes.meetings, query, all_of(ctx.meeting_scope, filter))
        folds = fold_chunks(hits)[:top_k]
        if not folds:
            return []
        attachments = await self.attachments(ctx, query, [f.meeting_id for f in folds])
        items = []
        for fold in folds:
            f = fold.chunks[0].doc.fields
            items.append(
                MeetingItem(
                    id=fold.meeting_id,
                    account_id=f["account_id"],
                    title=f["title"],
                    date=f["date"],
                    kind=f["kind"],
                    owner=f["owner"],
                    confidential=f["confidential"],
                    excerpts=[redact(h.doc.fields["body"]) for h in fold.chunks],
                    score=fold.score,
                    attachments=attachments.get(fold.meeting_id, []),
                )
            )
        return items

    async def attachments(
        self, ctx: RequestContext, query: str, meeting_ids: list[str]
    ) -> dict[str, list[AttachmentItem]]:
        """2단계: 찾은 회의의 첨부. 회의 id 목록과 별개로 권한 필터를 다시 건다."""
        scope = all_of(in_("meeting_id", meeting_ids), ctx.meeting_scope)
        hits = await self.search(ctx, self._indexes.attachments, query, scope)
        grouped: dict[str, list[AttachmentItem]] = {}
        for hit in hits:
            f = hit.doc.fields
            grouped.setdefault(f["meeting_id"], []).append(
                AttachmentItem(
                    id=hit.doc.id,
                    meeting_id=f["meeting_id"],
                    filename=f["filename"],
                    date=f["date"],
                    text=redact(f["body"]),
                    score=hit.score,
                )
            )
        return grouped
