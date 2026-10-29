"""영업 자료 인덱스 적재.

네 인덱스를 메모리에 만든다. 회의록은 청크 단위(`{meeting_id}#{seq}`)로 넣고, 회의 첨부는
부모 회의록의 `unit`·`confidential` 을 물려받아 회의록과 같은 권한 필터가 그대로 걸리게 한다.
고객 담당자의 전화·메일은 적재할 때 암호화한 필드(`phone_enc`, `email_enc`)로만 남기고
평문은 버린다 — 인덱스에 평문이 없으니 검색 결과·프롬프트·로그 어디로도 원문이 새지 않는다.

본문 마스킹(`redact`)은 여기서 하지 않고 검색 경계(`retrieval`)에서 출처와 상관없이 한 번 한다.
필터 가능 필드는 인덱스마다 명시하고, 표시에만 쓰는 값(본문·파일명 등)은 필드에 같이 두되
필터 대상에서는 뺀다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from chatcore.llm import Embedder
from chatcore.privacy import FieldCipher
from chatcore.search import Document, InMemoryIndex, SearchIndex, embed_documents

Record = dict[str, Any]

ACCOUNT_FILTERABLE = ("account_id", "name", "unit", "status", "industry", "kam")
CONTACT_FILTERABLE = ("account_id", "name", "title", "department", "role")
MEETING_FILTERABLE = (
    "meeting_id",
    "account_id",
    "unit",
    "confidential",
    "date",
    "kind",
    "seq",
    "title",
    "owner",
)
ATTACHMENT_FILTERABLE = ("meeting_id", "account_id", "unit", "confidential")


@dataclass(frozen=True)
class SalesData:
    """원본 JSON. 인덱스를 만든 뒤에는 들고 있지 않는다(연락처 평문이 들어 있다)."""

    accounts: list[Record]
    contacts: list[Record]
    meetings: list[Record]
    attachments: list[Record]


@dataclass(frozen=True)
class SalesIndexes:
    accounts: SearchIndex
    contacts: SearchIndex
    meetings: SearchIndex
    attachments: SearchIndex
    account_names: dict[str, str]  # account_id → 고객사 이름(질의 속 이름을 id 로 바꿀 때 대조)


def load_sales_data(data_dir: Path) -> SalesData:
    def read(name: str) -> list[Record]:
        return json.loads((data_dir / "sales" / f"{name}.json").read_text(encoding="utf-8"))

    return SalesData(read("accounts"), read("contacts"), read("meetings"), read("meeting_attachments"))


def account_docs(accounts: list[Record]) -> list[Document]:
    return [
        Document(
            id=a["id"],
            text=(
                f"{a['name']} {a['industry']}\n{a['summary']}\n"
                f"주력 제안: {a['main_offer']}\n영업 단계: {a['status']}"
            ),
            fields={
                "account_id": a["id"],
                "name": a["name"],
                "unit": a["unit"],
                "status": a["status"],
                "industry": a["industry"],
                "kam": a["kam"],
                "main_offer": a["main_offer"],
                "summary": a["summary"],
            },
        )
        for a in accounts
    ]


def contact_docs(
    contacts: list[Record], account_names: dict[str, str], cipher: FieldCipher
) -> list[Document]:
    # 검색 본문에는 연락처를 넣지 않는다. 원문은 암호문 필드로만 남는다.
    return [
        Document(
            id=c["id"],
            text=f"{c['name']} {c['title']} {c['department']} {c['role']}\n{c['note']}",
            fields={
                "account_id": c["account_id"],
                "account_name": account_names[c["account_id"]],
                "name": c["name"],
                "title": c["title"],
                "department": c["department"],
                "role": c["role"],
                "note": c["note"],
                "phone_enc": cipher.encrypt(c["phone"]),
                "email_enc": cipher.encrypt(c["email"]),
            },
        )
        for c in contacts
    ]


def meeting_chunk_docs(meetings: list[Record]) -> list[Document]:
    return [
        Document(
            id=f"{m['id']}#{chunk['seq']}",
            text=f"{m['title']}\n{chunk['text']}",
            fields={
                "meeting_id": m["id"],
                "account_id": m["account_id"],
                "unit": m["unit"],
                "confidential": m["confidential"],
                "date": m["date"],
                "kind": m["kind"],
                "seq": chunk["seq"],
                "title": m["title"],
                "owner": m["owner"],
                "body": chunk["text"],
            },
        )
        for m in meetings
        for chunk in m["chunks"]
    ]


def attachment_docs(attachments: list[Record], meetings: list[Record]) -> list[Document]:
    parents = {m["id"]: m for m in meetings}
    docs = []
    for a in attachments:
        parent = parents[a["meeting_id"]]
        docs.append(
            Document(
                id=a["id"],
                text=f"{a['filename']}\n{a['text']}",
                fields={
                    "meeting_id": a["meeting_id"],
                    "account_id": parent["account_id"],
                    # 권한 필드는 부모 회의록 값을 물려받는다.
                    "unit": parent["unit"],
                    "confidential": parent["confidential"],
                    "date": parent["date"],
                    "filename": a["filename"],
                    "body": a["text"],
                },
            )
        )
    return docs


async def build_indexes(data: SalesData, embedder: Embedder, cipher: FieldCipher) -> SalesIndexes:
    account_names = {a["id"]: a["name"] for a in data.accounts}
    parts = {
        "accounts": (account_docs(data.accounts), ACCOUNT_FILTERABLE),
        "contacts": (contact_docs(data.contacts, account_names, cipher), CONTACT_FILTERABLE),
        "meetings": (meeting_chunk_docs(data.meetings), MEETING_FILTERABLE),
        "meeting_attachments": (attachment_docs(data.attachments, data.meetings), ATTACHMENT_FILTERABLE),
    }
    built: dict[str, InMemoryIndex] = {}
    for name, (docs, filterable) in parts.items():
        await embed_documents(docs, embedder)
        built[name] = InMemoryIndex(name, docs, filterable=filterable)
    return SalesIndexes(
        accounts=built["accounts"],
        contacts=built["contacts"],
        meetings=built["meetings"],
        attachments=built["meeting_attachments"],
        account_names=account_names,
    )
