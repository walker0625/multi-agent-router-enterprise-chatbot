# 스트림 이벤트 계약

> `scripts/export_contract.py` 가 `chatcore.events` 에서 생성했습니다 (schema 1.0).
> 직접 고치지 말고 모델을 고친 뒤 다시 생성하세요.

## 이벤트 순서

- 라우터 → 웹: `turn` → `progress`(질문 분석) → `route`
  → 채널별 [`progress`* `references`? `delta`* `channel_done`] → (PARALLEL) `summary` 채널 → `done`
- HITL: `turn` → `route` → `hitl` → `done(status=hitl)`
- 도메인 에이전트 → 라우터: `progress`* → `references`? → `delta`* → `channel_done`
  (실패하면 `error` 하나로 끝난다)
- `done` 은 저장된 턴과 같은 내용이다. 인용 번호는 `done.answers` 에서 화면 순서대로 다시 매겨진다.
- 하트비트: 이벤트가 없을 때 SSE 주석 프레임(`: ping`)이 온다.

## ChannelDoneEvent

| 필드 | 타입 | 필수 |
|---|---|---|
| `type` | "channel_done" | 예 |
| `channel` | string \| null | 예 |
| `status` | ChannelStatus | 예 |
| `usage` | Usage | 예 |

## DeltaEvent

| 필드 | 타입 | 필수 |
|---|---|---|
| `type` | "delta" | 예 |
| `channel` | string \| null | 예 |
| `text` | string | 예 |

## DoneEvent

| 필드 | 타입 | 필수 |
|---|---|---|
| `type` | "done" | 예 |
| `turn_id` | string | 예 |
| `status` | TurnStatus | 예 |
| `outcome` | Outcome \| null | 예 |
| `answers` | Answer[] | 예 |
| `hitl` | HitlEvent \| null | 예 |
| `recommendations` | string[] | 예 |
| `usage` | Usage | 예 |
| `latency_ms` | integer | 예 |

## ErrorEvent

| 필드 | 타입 | 필수 |
|---|---|---|
| `type` | "error" | 예 |
| `channel` | string \| null | 예 |
| `code` | ErrorCode | 예 |
| `message` | string | 예 |

## HitlEvent

되묻기. `suggestions` 는 바로 보낼 수 있는 재질문, `choices` 는 도메인 버튼.

| 필드 | 타입 | 필수 |
|---|---|---|
| `type` | "hitl" | 예 |
| `stage` | string | 예 |
| `message` | string | 예 |
| `suggestions` | string[] | 예 |
| `choices` | DomainChoice[] | 예 |

## ProgressEvent

| 필드 | 타입 | 필수 |
|---|---|---|
| `type` | "progress" | 예 |
| `channel` | string \| null | 예 |
| `message` | string | 예 |
| `percent` | integer | 예 |

## ReferencesEvent

| 필드 | 타입 | 필수 |
|---|---|---|
| `type` | "references" | 예 |
| `channel` | string \| null | 예 |
| `items` | Reference[] | 예 |

## RouteEvent

| 필드 | 타입 | 필수 |
|---|---|---|
| `type` | "route" | 예 |
| `outcome` | Outcome | 예 |
| `agents` | string[] | 예 |
| `decided_by` | string | 예 |

## TurnEvent

| 필드 | 타입 | 필수 |
|---|---|---|
| `type` | "turn" | 예 |
| `chat_id` | string | 예 |
| `turn_id` | string | 예 |
| `turn_no` | integer | 예 |

## Answer

| 필드 | 타입 | 필수 |
|---|---|---|
| `channel` | string | 예 |
| `text` | string | 예 |
| `status` | ChannelStatus | 예 |
| `references` | Reference[] | 예 |

## ChannelStatus

값: `ok`, `truncated`, `error`, `stopped`

## DomainChoice

| 필드 | 타입 | 필수 |
|---|---|---|
| `domain` | string | 예 |
| `label` | string | 예 |
| `description` | string | 예 |

## ErrorCode

값: `INVALID_REQUEST`, `UNAUTHORIZED`, `FORBIDDEN`, `NOT_FOUND`, `CONFLICT`, `PAYLOAD_TOO_LARGE`, `TIMEOUT`, `CONTENT_FILTERED`, `OUTPUT_BLOCKED`, `UPSTREAM_ERROR`, `AGENT_UNAVAILABLE`, `SEARCH_FAILED`, `BUDGET_EXCEEDED`, `INTERNAL`

## Outcome

값: `SINGLE`, `PARALLEL`, `HITL`, `BLOCKED`

## Reference

답변 근거 카드. 본문의 `[N](cite:<id>)` 마커가 `id` 를 가리킨다.

| 필드 | 타입 | 필수 |
|---|---|---|
| `id` | string | 예 |
| `source` | string | 예 |
| `title` | string | 예 |
| `snippet` | string | 예 |
| `date` | string \| null | 예 |
| `url` | string \| null | 예 |
| `score` | number \| null | 예 |
| `meta` | object | 예 |

## TurnStatus

값: `pending`, `done`, `hitl`, `blocked`, `error`, `stopped`, `disconnected`

## Usage

| 필드 | 타입 | 필수 |
|---|---|---|
| `input_tokens` | integer | 예 |
| `output_tokens` | integer | 예 |
