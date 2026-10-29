"""입구 병합 — 직전 대화를 반영해 질문 하나를 자기완결적으로 다시 쓴다.

멀티턴 맥락은 여기서 한 번만 쓰고, 다시 쓴 질의를 모든 레이어가 같은 입력으로 받는다. 레이어
사이로 직전 턴을 흘려보내지 않는다. 다시 쓰기는 품질을 높이는 단계일 뿐이라 실패하거나 시간이
넘으면 원문으로 진행한다. 첫 턴에는 부르지 않는다.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from chatcore.agent_api import HistoryTurn
from chatcore.deadline import Deadline
from chatcore.errors import AppError
from chatcore.llm import ChatModel, Usage, system, user
from chatcore.prompts import VersionedFile

log = logging.getLogger(__name__)

MAX_TURNS = 2
CALL_CAP_S = 5.0
ANSWER_PREVIEW_CHARS = 300
MAX_QUERY_CHARS = 2_000


class Contextualizer:
    def __init__(self, chat: ChatModel, prompts: VersionedFile) -> None:
        self._chat = chat
        self._prompts = prompts

    @property
    def tag(self) -> str:
        return self._prompts.tag

    async def rewrite(
        self, query: str, history: Sequence[HistoryTurn], deadline: Deadline
    ) -> tuple[str, Usage]:
        recent = list(history)[-MAX_TURNS:]
        if not recent:
            return query, Usage()
        lines = []
        for turn in recent:
            lines += [f"사용자: {turn.question}", f"챗봇: {turn.answer[:ANSWER_PREVIEW_CHARS]}"]
        messages = [
            system(self._prompts.data["contextualize_system"]),
            user(self._prompts.render("contextualize_user", history="\n".join(lines), query=query)),
        ]
        try:
            completion = await self._chat.complete(
                messages, task="route.contextualize", deadline=deadline.child(CALL_CAP_S), max_tokens=200
            )
        except AppError as exc:
            log.warning("contextualize failed, using original query", extra={"code": exc.code.value})
            return query, Usage()
        rewritten = completion.text.strip()
        if completion.finish_reason == "length" or not rewritten:  # 잘린 질문보다 원문이 낫다
            return query, completion.usage
        return rewritten[:MAX_QUERY_CHARS], completion.usage
