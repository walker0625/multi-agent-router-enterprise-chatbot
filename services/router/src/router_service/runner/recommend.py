"""후속 질문 추천. 답변 뒤에 붙는 부가 기능이라 실패하면 빈 목록으로 넘어간다."""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence

from pydantic import BaseModel, ValidationError

from chatcore.deadline import Deadline
from chatcore.errors import AppError
from chatcore.events import Answer
from chatcore.llm import ChatModel, Usage, system, user
from chatcore.prompts import VersionedFile
from router_service.runner.citations import strip_markers

CALL_CAP_S = 8.0
ANSWER_PREVIEW_CHARS = 1_000
COUNT = 3

RECOMMEND_SCHEMA = {
    "type": "object",
    "properties": {"questions": {"type": "array", "items": {"type": "string"}}},
    "required": ["questions"],
    "additionalProperties": False,
}

log = logging.getLogger(__name__)


class _Questions(BaseModel):
    questions: list[str]


class Recommender:
    def __init__(self, chat: ChatModel, prompts: VersionedFile) -> None:
        self._chat = chat
        self._prompts = prompts

    async def recommend(
        self, query: str, domains: Sequence[str], answers: Sequence[Answer], deadline: Deadline
    ) -> tuple[list[str], Usage]:
        answer = "\n".join(strip_markers(a.text) for a in answers)[:ANSWER_PREVIEW_CHARS]
        messages = [
            system(self._prompts.data["recommend_system"]),
            user(
                self._prompts.render("recommend_user", domains=", ".join(domains), query=query, answer=answer)
            ),
        ]
        try:
            completion = await self._chat.complete(
                messages, task="recommend", deadline=deadline.child(CALL_CAP_S), json_schema=RECOMMEND_SCHEMA
            )
            questions = _Questions.model_validate(json.loads(completion.text)).questions
        except (AppError, json.JSONDecodeError, ValidationError) as exc:
            log.warning("recommend failed", extra={"error": type(exc).__name__})
            return [], Usage()
        return [q.strip() for q in questions if q.strip()][:COUNT], completion.usage
