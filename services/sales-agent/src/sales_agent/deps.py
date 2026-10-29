"""런타임 의존성 조립.

포트(ChatModel·Embedder·SearchIndex)와 연락처 암호화기·프롬프트를 한 곳에서 만든다. 파일
읽기·임베딩·인덱스 생성은 여기서만 하고 FastAPI lifespan 에서 부른다 — 임포트 시점에는
I/O 가 없다. 테스트는 fake 를 담은 `Deps` 를 직접 만들어 주입한다.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from chatcore.llm import ChatModel, Embedder, build_llm
from chatcore.privacy import FieldCipher
from sales_agent.fakes import make_fake_handlers
from sales_agent.indexes import SalesIndexes, build_indexes, load_sales_data
from sales_agent.prompts import SalesPrompts
from sales_agent.settings import Settings

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Deps:
    chat: ChatModel
    embedder: Embedder
    indexes: SalesIndexes
    cipher: FieldCipher
    prompts: SalesPrompts


def field_cipher(settings: Settings) -> FieldCipher:
    if settings.field_key_b64 is not None:
        return FieldCipher.from_b64(settings.field_key_b64.get_secret_value())
    # prod 는 설정 검증에서 이미 막혔다. dev·test 는 데이터를 매번 다시 암호화하므로 임시 키로 충분하다.
    log.warning(
        "SALES_FIELD_KEY_B64 가 없어 임시 키로 연락처를 암호화합니다", extra={"app_env": settings.app_env}
    )
    return FieldCipher(os.urandom(32))


async def build_deps(settings: Settings) -> Deps:
    data = load_sales_data(settings.data_dir)
    chat, embedder = build_llm(settings.llm, make_fake_handlers([a["name"] for a in data.accounts]))
    cipher = field_cipher(settings)
    indexes = await build_indexes(data, embedder, cipher)
    return Deps(chat=chat, embedder=embedder, indexes=indexes, cipher=cipher, prompts=SalesPrompts.load())
