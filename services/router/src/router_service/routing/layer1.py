"""L1 — 키워드 규칙.

키워드는 각 도메인 데이터에서 그 도메인에만 나오는 고유 어휘다. 매칭 방식은 언어나 길이로
추정하지 않고 YAML 에서 어느 목록에 들어 있느냐로만 정한다.
- exact: 키워드 앞뒤가 영문·숫자가 아니어야 한다. 뒤에 한글 조사가 붙는 건 허용한다
  ("CR 현황"·"CR이" 는 매칭, "CRM" 은 아님). 대소문자는 무시한다.
- partial: 공백·대소문자를 무시한 부분 문자열.
정확히 한 도메인만 걸리고 복합 질문 단서가 없을 때만 결정한다. 여러 도메인이 걸린 건 모호하다는
뜻이지 두 갈래 질문이라는 뜻이 아니므로 L1 은 PARALLEL 을 정하지 않는다.
"""

from __future__ import annotations

import re
from pathlib import Path

from chatcore.deadline import Deadline
from chatcore.events import Outcome
from chatcore.prompts import VersionedFile
from router_service.routing.base import Domain, LayerResult

_SPACES = re.compile(r"\s+")


def _norm(text: str) -> str:
    return _SPACES.sub("", text).casefold()


def _exact_pattern(keyword: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![0-9A-Za-z]){re.escape(keyword)}(?![0-9A-Za-z])", re.IGNORECASE)


class KeywordLayer:
    def __init__(self, file: VersionedFile) -> None:
        self._file = file
        self._exact: dict[Domain, list[tuple[str, re.Pattern[str]]]] = {}
        self._partial: dict[Domain, list[tuple[str, str]]] = {}
        owner: dict[str, Domain] = {}
        for name, lists in file.data["domains"].items():
            domain = Domain(name)
            exact = list(lists.get("exact") or [])
            partial = list(lists.get("partial") or [])
            for keyword in exact + partial:
                key = _norm(keyword)
                if key in owner and owner[key] != domain:
                    raise ValueError(
                        f"{file.path}: 키워드 '{keyword}' 가 {owner[key]} 와 {domain} 에 함께 있습니다"
                    )
                owner[key] = domain
            self._exact[domain] = [(k, _exact_pattern(k)) for k in exact]
            self._partial[domain] = [(k, _norm(k)) for k in partial]
        self._cues = [(c, _norm(c)) for c in file.data.get("multi_part_cues") or []]

    @classmethod
    def load(cls, path: Path) -> KeywordLayer:
        return cls(VersionedFile.load(path))

    @property
    def name(self) -> str:
        return "L1"

    @property
    def tags(self) -> list[str]:
        return [self._file.tag]

    def _match(self, query: str) -> dict[Domain, list[str]]:
        normalized = _norm(query)
        matched: dict[Domain, list[str]] = {}
        for domain in self._exact:
            hits = [k for k, pattern in self._exact[domain] if pattern.search(query)]
            hits += [k for k, key in self._partial[domain] if key in normalized]
            if hits:
                matched[domain] = hits
        return matched

    async def route(self, query: str, deadline: Deadline) -> LayerResult:
        matched = self._match(query)
        normalized = _norm(query)
        cues = [c for c, key in self._cues if key in normalized]
        detail = {"matched": {d.value: hits for d, hits in matched.items()}, "cues": cues}
        if not matched:
            return LayerResult(layer=self.name, status="undecided", reason="no_match", detail=detail)
        if cues:
            return LayerResult(layer=self.name, status="undecided", reason="multi_part", detail=detail)
        if len(matched) > 1:
            return LayerResult(layer=self.name, status="undecided", reason="ambiguous", detail=detail)
        return LayerResult(
            layer=self.name,
            status="decided",
            outcome=Outcome.SINGLE,
            domains=list(matched),
            reason="keyword",
            detail=detail,
        )
