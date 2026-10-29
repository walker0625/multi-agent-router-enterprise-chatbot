"""수행 자료 인덱스 — 메일·회의 메모·첨부 세 개.

본문 마스킹(전화번호·이메일)은 적재할 때 한 번만 한다. 그 뒤로는 인덱스·프롬프트·참조 카드가
모두 마스킹된 텍스트만 보므로, 경로마다 마스킹을 챙기다 한 곳을 빠뜨릴 일이 없다.
필터는 인덱스마다 허용한 필드로만 걸린다. 권한 범위(`project_id`)는 세 인덱스 모두 허용하고,
질의 기간(`date`)은 메일·메모만 허용한다.

메일·메모 본문 앞에는 프로젝트 코드를 붙인다. 사용자는 "P-101 일정"처럼 코드로 묻는데 제목·본문에는
코드가 없어서, 그대로 두면 검색·리랭크가 그 프로젝트 자료를 앞에 두지 못한다(첨부는 파일명에 이미
코드가 있다).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from chatcore.llm import Embedder
from chatcore.privacy import redact
from chatcore.search import Document, InMemoryIndex, SearchIndex, embed_documents


@dataclass(frozen=True)
class Source:
    index: SearchIndex
    dated: bool  # 질의 기간 필터(date)를 거는 인덱스인가


@dataclass(frozen=True)
class DeliveryIndexes:
    sources: tuple[Source, ...]
    project_names: Mapping[str, str]


def _read(path: Path) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_documents(data_dir: Path) -> tuple[list[Document], list[Document], list[Document]]:
    root = data_dir / "delivery"
    mails, notes, files = (
        _read(root / "mails.json"),
        _read(root / "notes.json"),
        _read(root / "attachments.json"),
    )
    mail_dates = {m["id"]: m["date"] for m in mails}
    mail_docs = [
        Document(
            m["id"],
            redact(f"{m['project_id']} {m['subject']}\n{m['body']}"),
            {
                "source": "mail",
                "project_id": m["project_id"],
                "date": m["date"],
                "topic": m["topic"],
                "sender": m["sender"],
                "subject": redact(m["subject"]),
            },
        )
        for m in mails
    ]
    note_docs = [
        Document(
            n["id"],
            redact(f"{n['project_id']} {n['title']}\n{n['body']}"),
            {
                "source": "note",
                "project_id": n["project_id"],
                "date": n["date"],
                "kind": n["kind"],
                "author": n["author"],
                "title": redact(n["title"]),
            },
        )
        for n in notes
    ]
    file_docs = [
        Document(
            f["id"],
            redact(f"{f['filename']}\n{f['text']}"),
            {
                "source": "attachment",
                "project_id": f["project_id"],
                "parent_id": f["parent_id"],
                "parent_type": f["parent_type"],
                "filename": redact(f["filename"]),
                "date": mail_dates[f["parent_id"]],  # 첨부의 날짜는 부모 메일의 날짜
            },
        )
        for f in files
    ]
    return mail_docs, note_docs, file_docs


async def build_indexes(data_dir: Path, embedder: Embedder) -> DeliveryIndexes:
    mails, notes, files = load_documents(data_dir)
    await embed_documents([*mails, *notes, *files], embedder)
    projects = _read(data_dir / "delivery" / "projects.json")
    return DeliveryIndexes(
        sources=(
            Source(InMemoryIndex("mails", mails, filterable={"project_id", "date"}), dated=True),
            Source(InMemoryIndex("notes", notes, filterable={"project_id", "date"}), dated=True),
            Source(InMemoryIndex("attachments", files, filterable={"project_id"}), dated=False),
        ),
        project_names={p["id"]: p["name"] for p in projects},
    )
