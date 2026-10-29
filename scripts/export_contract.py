"""스트림 이벤트 계약을 내보낸다 — 웹 TS 타입의 원천과 사람이 읽는 계약 문서.

    uv run python scripts/export_contract.py

- `web/src/api/contract.schema.json`: `npm run gen:types` 가 TS 타입으로 바꾼다.
- `docs/api-contract.md`: 이벤트별 필드 표. 손으로 고치지 않는다.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from chatcore.events import EVENT_ADAPTER, SCHEMA_VERSION

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_OUT = ROOT / "web/src/api/contract.schema.json"
DOC_OUT = ROOT / "docs/api-contract.md"

ORDER_NOTE = """\
## 이벤트 순서

- 라우터 → 웹: `turn` → `progress`(질문 분석) → `route`
  → 채널별 [`progress`* `references`? `delta`* `channel_done`] → (PARALLEL) `summary` 채널 → `done`
- HITL: `turn` → `route` → `hitl` → `done(status=hitl)`
- 도메인 에이전트 → 라우터: `progress`* → `references`? → `delta`* → `channel_done`
  (실패하면 `error` 하나로 끝난다)
- `done` 은 저장된 턴과 같은 내용이다. 인용 번호는 `done.answers` 에서 화면 순서대로 다시 매겨진다.
- 하트비트: 이벤트가 없을 때 SSE 주석 프레임(`: ping`)이 온다.
"""


def _type_of(prop: dict[str, Any]) -> str:
    if "$ref" in prop:
        return prop["$ref"].rsplit("/", 1)[-1]
    if "const" in prop:
        return f'"{prop["const"]}"'
    if "anyOf" in prop:
        return " \\| ".join(_type_of(p) for p in prop["anyOf"])
    if prop.get("type") == "array":
        return f"{_type_of(prop.get('items', {}))}[]"
    if prop.get("type") == "object":
        return "object"
    return str(prop.get("type", "any"))


def render_doc(schema: dict[str, Any]) -> str:
    defs: dict[str, Any] = schema.get("$defs", {})
    lines = [
        "# 스트림 이벤트 계약",
        "",
        f"> `scripts/export_contract.py` 가 `chatcore.events` 에서 생성했습니다 (schema {SCHEMA_VERSION}).",
        "> 직접 고치지 말고 모델을 고친 뒤 다시 생성하세요.",
        "",
        ORDER_NOTE,
    ]
    for name in sorted(defs, key=lambda n: (not n.endswith("Event"), n)):
        model = defs[name]
        lines += [f"## {name}", ""]
        if doc := model.get("description"):
            lines += [doc.strip(), ""]
        if "enum" in model:
            lines += ["값: " + ", ".join(f"`{v}`" for v in model["enum"]), ""]
            continue
        required = set(model.get("required", []))
        lines += ["| 필드 | 타입 | 필수 |", "|---|---|---|"]
        for field, prop in model.get("properties", {}).items():
            lines.append(f"| `{field}` | {_type_of(prop)} | {'예' if field in required else ''} |")
        lines.append("")
    return "\n".join(lines)


def _as_received(schema: dict[str, Any]) -> None:
    """웹이 실제로 받는 모양으로 맞춘다.

    - 이벤트는 모든 필드를 항상 직렬화하므로 기본값이 있는 필드도 필수로 표시한다.
      그래야 TS 에서 `type` 으로 이벤트 종류를 좁힐 수 있다.
    - 정의되지 않은 필드는 없으므로 추가 속성을 막는다.
    - 필드 title 은 지운다. 남겨 두면 TS 생성기가 필드마다 별칭 타입을 만든다.
    """
    for model in schema.get("$defs", {}).values():
        props = model.get("properties")
        if not props:
            continue
        for prop in props.values():
            prop.pop("title", None)
        model["required"] = list(props)
        model["additionalProperties"] = False


def main() -> None:
    schema = EVENT_ADAPTER.json_schema(mode="serialization")
    schema["title"] = "StreamEvent"
    _as_received(schema)
    SCHEMA_OUT.parent.mkdir(parents=True, exist_ok=True)
    SCHEMA_OUT.write_text(json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    DOC_OUT.write_text(render_doc(schema), encoding="utf-8")
    print(f"wrote {SCHEMA_OUT.relative_to(ROOT)}, {DOC_OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
