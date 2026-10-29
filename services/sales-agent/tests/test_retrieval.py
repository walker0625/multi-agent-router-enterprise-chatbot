import json
from collections.abc import Callable

from chatcore.auth import Principal
from chatcore.llm import ToolCall
from chatcore.search import Document, Hit
from sales_agent.context import RequestContext
from sales_agent.deps import Deps
from sales_agent.privacy import HIDDEN
from sales_agent.retrieval import ContactItem, MeetingItem, Retriever, fold_chunks, resolve_account
from sales_agent.tools import TOOL_NAME, execute

CONFIDENTIAL_S1 = {"M-003", "M-005"}  # 한빛유통 가격 협상·계약 조건 협의(S1 비공개)


def _hit(meeting_id: str, seq: int, score: float) -> Hit:
    doc = Document(f"{meeting_id}#{seq}", "", {"meeting_id": meeting_id, "seq": seq})
    return Hit(doc=doc, score=score)


def test_fold_keeps_top_two_chunks_in_seq_order() -> None:
    hits = [_hit("M-1", 2, 0.9), _hit("M-2", 1, 0.8), _hit("M-1", 3, 0.5), _hit("M-1", 1, 0.1)]
    folds = fold_chunks(hits)
    assert [f.meeting_id for f in folds] == ["M-1", "M-2"]
    first = folds[0]
    assert first.score == 0.9  # 회의 점수 = 청크 최대값
    assert [h.doc.fields["seq"] for h in first.chunks] == [2, 3]  # 상위 2개(0.9, 0.5)를 seq 순으로


def test_fold_orders_meetings_by_best_chunk() -> None:
    folds = fold_chunks([_hit("A", 1, 0.2), _hit("B", 1, 0.3), _hit("A", 2, 0.7)])
    assert [(f.meeting_id, f.score) for f in folds] == [("A", 0.7), ("B", 0.3)]


def test_account_name_resolution() -> None:
    names = {"A-01": "한빛유통", "A-02": "새솔물류"}
    assert resolve_account("한빛 유통", names) == "A-01"
    assert resolve_account("(주)한빛유통", names) == "A-01"
    assert resolve_account("한빛", names) is None  # 부분 이름은 추측하지 않는다
    assert resolve_account("한빛유통 새솔물류", names) is None  # 둘 이상이면 모른다


async def _meetings(retriever: Retriever, ctx: RequestContext, query: str) -> list[MeetingItem]:
    account = retriever.account_id("한빛유통")
    found = await retriever.find(
        ctx, "meeting", query, account_id=account, date_from=None, date_to=None, top_k=10
    )
    assert all(isinstance(m, MeetingItem) for m in found)
    return [m for m in found if isinstance(m, MeetingItem)]


async def test_confidential_meetings_and_attachments_follow_sales_units(
    retriever: Retriever,
    make_ctx: Callable[..., RequestContext],
    seller: Principal,
    outsider: Principal,
    s2_lead: Principal,
) -> None:
    query = "한빛유통 가격 협상 수정 견적"
    mine = await _meetings(retriever, make_ctx(seller), query)
    assert CONFIDENTIAL_S1 <= {m.id for m in mine}
    attachments = {a.id for m in mine for a in m.attachments}
    assert "MA-02" in attachments  # M-003(비공개)의 첨부

    for other in (outsider, s2_lead):  # 단위가 없거나 다른 단위(S2)
        theirs = await _meetings(retriever, make_ctx(other), query)
        assert theirs and not CONFIDENTIAL_S1 & {m.id for m in theirs}
        assert all(not m.confidential for m in theirs)
        assert "MA-02" not in {a.id for m in theirs for a in m.attachments}


async def test_attachment_hop_rechecks_permission_even_for_given_meeting_ids(
    retriever: Retriever, make_ctx: Callable[..., RequestContext], seller: Principal, outsider: Principal
) -> None:
    assert await retriever.attachments(make_ctx(outsider), "수정 견적", ["M-003"]) == {}
    allowed = await retriever.attachments(make_ctx(seller), "수정 견적", ["M-003"])
    assert [a.id for a in allowed["M-003"]] == ["MA-02"]


async def test_contacts_decrypted_only_with_claim(
    retriever: Retriever, make_ctx: Callable[..., RequestContext], seller: Principal, outsider: Principal
) -> None:
    account = retriever.account_id("한빛유통")

    async def contacts(principal: Principal) -> list[ContactItem]:
        found = await retriever.find(
            make_ctx(principal),
            "contact",
            "IT전략팀 담당자",
            account_id=account,
            date_from=None,
            date_to=None,
            top_k=5,
        )
        return [c for c in found if isinstance(c, ContactItem)]

    mine = await contacts(seller)
    assert mine[0].name == "권나연"
    assert (mine[0].phone, mine[0].email) == ("010-0000-1001", "contact02@a-01.example")

    theirs = await contacts(outsider)
    assert theirs[0].name == "권나연"
    assert all((c.phone, c.email) == (HIDDEN, HIDDEN) for c in theirs)


async def test_index_holds_only_encrypted_contacts(deps: Deps) -> None:
    [doc] = await deps.indexes.contacts.get(["C-02"])
    assert "phone" not in doc.fields and "email" not in doc.fields
    assert doc.fields["phone_enc"].startswith("v1.")
    assert "010-0000-1001" not in json.dumps(doc.fields) + doc.text


async def test_phone_in_meeting_body_is_masked_in_tool_result_and_cards(
    retriever: Retriever, make_ctx: Callable[..., RequestContext], seller: Principal
) -> None:
    # 연락처 열람 권한이 있어도 회의록 본문 속 번호는 검색 경계에서 가린다.
    args = {"intents": ["meeting"], "query": "니즈 파악 고객 담당 연락처 송현우", "account_name": "한빛유통"}
    outcome = await execute(
        ToolCall(id="c1", name=TOOL_NAME, arguments=json.dumps(args, ensure_ascii=False)),
        make_ctx(seller),
        retriever,
    )
    cards = json.dumps([r.model_dump() for r in outcome.references], ensure_ascii=False)
    assert "[전화번호]" in outcome.content
    assert "010-0000-1000" not in outcome.content
    assert "010-0000-1000" not in cards


async def test_intents_get_separate_result_lists(
    retriever: Retriever, make_ctx: Callable[..., RequestContext], seller: Principal
) -> None:
    args = {
        "intents": ["account", "contact", "meeting"],
        "query": "한빛유통 협상",
        "account_name": "한빛유통",
    }
    outcome = await execute(
        ToolCall(id="c1", name=TOOL_NAME, arguments=json.dumps(args, ensure_ascii=False)),
        make_ctx(seller),
        retriever,
    )
    assert outcome.evidence is not None
    results = outcome.evidence["results"]
    assert set(results) == {"account", "contact", "meeting"}
    for intent, items in results.items():
        assert items and all(item["cite_id"].startswith(f"{intent}:") for item in items)
    assert outcome.evidence["account"] == "한빛유통"
    # 도구 결과의 cite_id 와 근거 카드 id 는 같은 결과에서 나온다.
    cited = {i["cite_id"] for items in results.values() for i in items}
    cited |= {a["cite_id"] for m in results["meeting"] for a in m["attachments"]}
    assert cited == {r.id for r in outcome.references}


async def test_same_search_is_memoized_within_a_turn(
    recorded: Callable[..., tuple[Deps, list]], make_ctx: Callable[..., RequestContext], seller: Principal
) -> None:
    deps, log = recorded()
    retriever = Retriever(deps.indexes, deps.embedder, deps.cipher)
    ctx = make_ctx(seller)
    args = json.dumps({"intents": ["account", "meeting"], "query": "한빛유통 견적"}, ensure_ascii=False)
    first = await execute(ToolCall(id="c1", name=TOOL_NAME, arguments=args), ctx, retriever)
    searches = ctx.budget.searches
    second = await execute(ToolCall(id="c2", name=TOOL_NAME, arguments=args), ctx, retriever)

    assert [name for _, name, _ in log] == ["accounts", "meetings", "meeting_attachments"]
    assert ctx.budget.searches == searches == 3
    assert first.content == second.content

    # 다른 요청(새 컨텍스트)은 메모를 공유하지 않는다.
    await execute(ToolCall(id="c3", name=TOOL_NAME, arguments=args), make_ctx(seller), retriever)
    assert len(log) == 6
