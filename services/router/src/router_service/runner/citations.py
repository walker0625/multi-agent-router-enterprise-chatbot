"""인용 마커 재번호.

에이전트는 각자 1번부터 번호를 매긴다. PARALLEL 에서 두 채널의 [1] 이 서로 다른 근거를 가리키지
않도록, 화면 표시 순서(채널 순서 → 본문 등장 순서)로 처음 나온 근거 ID 부터 다시 매긴다. 같은 ID 는
같은 번호다. 근거 카드에 없는 ID 를 가리키는 마커는 지운다(근거 없는 인용을 보이지 않는다).
카드는 번호 순으로 정렬하고, 인용되지 않은 카드는 뒤에 둔다.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from chatcore.events import Answer

MARKER = re.compile(r"\[(\d+)\]\(cite:([^)\s]+)\)")


def strip_markers(text: str) -> str:
    return MARKER.sub("", text)


def renumber(answers: Sequence[Answer]) -> list[Answer]:
    numbers: dict[str, int] = {}
    out: list[Answer] = []
    for answer in answers:
        known = {r.id for r in answer.references}
        cited_here: set[str] = set()

        def replace(match: re.Match[str], known: set[str] = known, cited_here: set[str] = cited_here) -> str:
            ref_id = match.group(2)
            if ref_id not in known:
                return ""
            cited_here.add(ref_id)
            n = numbers.setdefault(ref_id, len(numbers) + 1)
            return f"[{n}](cite:{ref_id})"

        text = MARKER.sub(replace, answer.text)
        cited = sorted((r for r in answer.references if r.id in cited_here), key=lambda r: numbers[r.id])
        uncited = [r for r in answer.references if r.id not in cited_here]
        out.append(answer.model_copy(update={"text": text, "references": cited + uncited}))
    return out
