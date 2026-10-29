from chatcore.deadline import Deadline
from chatcore.filters import Eq
from chatcore.llm.fake import HashingEmbedder
from chatcore.search import Document, InMemoryIndex, LexicalReranker, embed_documents, tokenize


def test_tokenize_adds_hangul_bigrams() -> None:
    tokens = tokenize("일정이 지연됐다")
    assert "일정이" in tokens
    assert "일정" in tokens  # bigram 이 조사 붙은 어형끼리 겹치게 한다


async def _index() -> InMemoryIndex:
    docs = [
        Document("d1", "물류 시스템 구축 일정이 2주 지연되었습니다", {"project_id": "P-1"}),
        Document("d2", "결제 모듈 장애 원인 분석 보고", {"project_id": "P-1"}),
        Document("d3", "물류 창고 자동화 견적 회의", {"project_id": "P-2"}),
    ]
    await embed_documents(docs, HashingEmbedder(256))
    return InMemoryIndex("t", docs, filterable={"project_id"})


async def test_hybrid_search_ranks_relevant_first() -> None:
    index = await _index()
    query = "물류 구축 일정 지연"
    vec = HashingEmbedder(256).embed_one(query)
    hits = await index.search(text=query, vector=vec, top_k=3)
    assert hits[0].doc.id == "d1"
    assert {"bm25", "vector", "fused"} <= hits[0].scores.keys()


async def test_filter_applies_before_ranking() -> None:
    index = await _index()
    hits = await index.search(text="물류", filter=Eq("project_id", "P-2"), top_k=5)
    assert [h.doc.id for h in hits] == ["d3"]


async def test_no_signal_returns_empty() -> None:
    index = await _index()
    assert await index.search(text="전혀 없는 단어조합", top_k=5) == []


async def test_lexical_reranker_scores_in_input_order() -> None:
    index = await _index()
    docs = await index.get(["d2", "d1"])
    scores = (await LexicalReranker().rerank("물류 일정 지연", docs, deadline=Deadline(5))).scores
    assert scores[1] > scores[0]
    assert all(0 <= s <= 1 for s in scores)
