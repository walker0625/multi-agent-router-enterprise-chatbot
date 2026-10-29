# 라우터(수퍼바이저) 명세 — `services/router`, 패키지 `router_service`

웹 요청을 받아 질문을 어느 에이전트에 보낼지 정하고, 하위 스트림을 하나로 합쳐 SSE로
내보내며, 대화를 저장한다. 토큰을 발급하는 유일한 서비스이기도 하다.
공통 규약은 `docs/spec/conventions.md`를 따른다.

## 1. 3단 캐스케이드 라우팅 (`router_service/routing/`)

### 원칙 (지켜야 하는 설계 제약)
1. **레이어 격리**
   - 레이어마다 `async def route(self, query: str, deadline: Deadline) -> LayerResult` 하나만 노출한다.
   - 위 레이어의 점수나 판단을 아래 레이어로 넘기지 않는다. 모든 레이어는 같은 `query`를 받는다.
   - 각 레이어는 떼어 내도 캐스케이드가 동작해야 한다. 설정이 없는 레이어는 `skipped`로 기록한다. "판단 못 함"과는 구분한다.
2. **멀티턴 맥락은 입구에서만 병합한다.** 직전 대화가 있으면 `contextualize`가 자기완결적 질의 하나로 다시 쓰고, 그 질의를 모든 레이어가 받는다. 레이어 사이에 직전 턴을 주입하지 않는다.
3. **가중치 점수를 쓰지 않는다.** 이진 규칙으로만 판정한다. 레이어마다 결정 조건은 "정확히 한 도메인"이다.
4. **LLM 확신도(confidence)를 임계값으로 쓰지 않는다.** L3는 도메인별 YES/NO와 근거 한 줄만 받는다.
5. **판정 재료는 버전 있는 YAML에 둔다**(`services/router/routing/*.yaml`). 파일 태그(해시)는 trace에 남긴다.
6. **특정 도메인을 특정 레이어에서 하드코딩으로 빼지 않는다.** 데이터가 생기면 어느 도메인이든 어느 레이어에서든 결정될 수 있어야 한다.

### 타입 (`base.py`)
- `Domain(StrEnum)`: `delivery`, `sales`, `general`
- `LayerResult`
  - 필드: `layer: str`, `status: "decided" | "undecided" | "skipped"`, `outcome: Outcome | None`, `domains: list[Domain]`, `reason: str`, `detail: dict`
  - `detail`은 trace용이다. 매칭된 키워드, top-3 점수, τ, 판정 목록 등을 담는다.
- `RouteResult`
  - 필드: `outcome`, `domains`, `decided_by`(L1|L2|L3), `query`(병합 후), `original_query`, `trace: RouteTrace`
- `RouteTrace`
  - 필드: 레이어별 `LayerResult` 목록, 사용한 YAML 태그들, 모델·임베더 이름, 소요 ms

### L1 — 키워드 규칙 (`layer1.py`, `routing/keywords.yaml`)
- **YAML 형식:** `version`, `domains: {delivery: {exact: [...], partial: [...]}, sales: {...}, general: {...}}`, `multi_part_cues: [...]`
- **매칭**
  - `exact`: 앞뒤 경계 매칭이다. 키워드 앞뒤가 영숫자이면 안 된다. 한글 조사가 뒤에 붙는 건 허용한다.
    - 예: `CR`은 "CR 현황"·"CR이"에는 매칭되고 "CRM"에는 매칭되지 않는다.
  - `partial`: 부분 문자열 매칭이다.
  - exact로 갈지 partial로 갈지는 언어나 길이로 추정하지 않는다. **YAML에 어느 목록에 들어 있느냐**로만 정한다.
- **결정 규칙**
  - 매칭된 도메인이 정확히 1개이고 `multi_part_cues`("그리고", "와 함께", "둘 다", "동시에", "랑 같이" 등)가 없으면 `decided`, SINGLE이다.
  - 2개 이상이거나 복합 질문 단서가 있으면 `undecided`다. 사유는 `ambiguous` 또는 `multi_part`다.
    - **L1은 PARALLEL을 결정하지 않는다.** 여러 키워드가 걸렸다는 건 모호하다는 뜻이지 두 갈래 질문이라는 뜻이 아니다.
  - 0개면 `undecided`다.
- **적재 시 검사:** 같은 키워드가 두 도메인에 들어 있으면 시작을 거부한다(`ValueError`).
- **키워드 작성:** 각 도메인 데이터(`data/`)에서 그 도메인에만 나오는 고유 어휘를 고른다.
  - delivery 예: CR, UAT, SIT, 기성, 검수, 주간보고, 컷오버, 장애 보고
  - sales 예: KAM, 견적, 영업 단계, 제안 설명회, 경쟁사, 수주 가능성
  - general 예: 사용법, 대화 기록, 다시 생성, 출장비, 보안 교육
  - **애매한 단어는 넣지 않는다.** 예: "일정", "회의", "고객"은 두 도메인 모두에 나온다.

### L2 — 벡터 단일 게이트 (`layer2.py`, `routing/anchors.yaml`, `routing/thresholds.yaml`)
- **anchors.yaml:** `version`, `anchors: [{text, domain}]`. 도메인당 30~40개의 대표 질문을 손으로 쓴다(합성).
  - **평가 골드셋(`eval/`)과 겹치면 안 된다.** 골드셋은 나중에 따로 만든다.
- **thresholds.yaml:** `version`, `tau`, `embedder`(보정에 쓴 임베더 이름), `calibrated_on`(eval run-id 또는 `"initial-guess"`)
- **동작:** 시작할 때 앵커를 임베딩해 `InMemoryIndex`(벡터만)에 넣는다. 질의 임베딩의 top-3 코사인 중 τ 이상인 도메인을 모은다.
  - 정확히 1개 도메인이면 `decided`, SINGLE이다.
  - 아니면 `undecided`다. `detail`에 top-3 (domain, score), τ를 남긴다.
- **τ는 임베더별 값이다.** 현재 임베더 이름이 `thresholds.yaml`의 `embedder`와 다르면 L2를 `skipped`로 두고 경고 로그를 남긴다. 다른 모델의 τ를 그대로 쓰면 조용히 오판한다.
  - fake 임베더(`hashing-512`)용 초기값은 앵커 변형 문장으로 대략 잡는다. 최종 값은 eval의 `calibrate-l2`가 골드셋 dev 분할에서 정하고 test 분할로 확인한다.

### L3 — LLM 판정 (`layer3.py`, `routing/judge.yaml`)
- **judge.yaml:** `version`, 도메인 정의(각 도메인이 다루는 것과 다루지 않는 것), few-shot 6~9개, 지시문
- **출력 스키마:** `{"verdicts": [{"domain": "delivery|sales|general", "answer": "YES|NO", "reason": "..."}]}`. 도메인마다 하나씩이다.
- **판정 규칙**
  - YES인데 `reason`이 비어 있으면 NO로 본다.
  - general YES와 도메인(delivery/sales) YES가 함께 나오면 general은 뺀다.
  - YES가 0개면 HITL, 1개면 SINGLE, delivery·sales 둘 다 YES면 PARALLEL이다.
- **실패 처리**
  - JSON 파싱·스키마 검증이 실패하면 최대 2번 다시 시도한다. 그래도 실패하면 HITL이다(사유 `judge_failed`).
  - `ContentFiltered`는 BLOCKED다.
  - `DeadlineExceeded`는 HITL로 떨어뜨리지 말고 위로 올린다. 시간이 없으면 턴 자체가 TIMEOUT이다.
- task 라벨은 `route.judge`다.

### 입구 병합 (`contextualize.py`)
- 직전 완료 턴(최대 2개)이 있을 때만 `route.contextualize` LLM을 부른다. 질문 하나를 자기완결적으로 다시 쓴다.
- 실패하거나 시간이 초과되면 원문을 그대로 쓴다.
- 첫 턴에는 호출하지 않는다.

### 캐스케이드 (`cascade.py`)
- `Cascade(layers=[l1, l2, l3])`는 처음으로 `decided`가 나온 레이어에서 멈춘다.
- 모든 레이어가 결정하지 못하면 HITL이다. L3가 skipped인 구성도 마찬가지다.
- 판정 trace는 한 턴에 JSON 한 줄로 로그에 남기고 턴 저장소에도 저장한다. 평가와 운영이 같은 trace를 쓴다.

## 2. 턴 실행기 (`router_service/runner/`)
스트림 핸들러 하나에 모든 단계를 몰면 일반 질문·강제 도메인·다시 생성마다 파이프라인이 복사된다. 그래서 단계와 plan 출처를 나눈다.

- **`TurnRunner.run(plan_source, ...) -> AsyncIterator[Event]`**
  - 단계: `route`(plan 만들기) → `execute`(결과별 strategy) → `finalize` → `persist`
- **plan 출처**는 셋이고, runner는 하나다.
  - `CascadeSource`: 일반 질문
  - `ForcedSource`: 웹이 `domain`을 지정했거나 HITL 도메인 버튼을 눌렀을 때
  - `ReplaySource`: 다시 생성. 저장된 plan을 재사용하고 다시 라우팅하지 않는다.
- **strategy (결과별)**
  - **SINGLE:** 해당 `AgentClient` 스트림을 그대로 전달한다. `channel`은 도메인 이름으로 채운다.
  - **PARALLEL**
    - 에이전트마다 pump 태스크를 하나씩 두고 **bounded `asyncio.Queue`**(예: 64)에 넣는다. 꺼내 쓰는 쪽은 runner 하나다.
    - 센티넬로 채널 종료를 알린다. 채널별 오류는 그 채널에서만 처리한다.
    - 끝나면 pump를 취소하고 정리한다.
    - **둘 다 ok일 때만** `summary` 채널로 요약을 스트리밍한다(task `summary`).
  - **HITL** (`hitl.py`)
    - 1단계(`stage=rewrite`): `hitl.suggest` LLM이 재질문 2~3개를 제안한다.
      - 제안에 프로젝트를 넣을 때는 `{unit:P-101}` 자리표시자를 쓰게 하고, 사용자 `project_ids`와 대조한 뒤 프로젝트 이름으로 바꾼다.
      - 허용 목록에 없는 자리표시자가 들어 있으면 그 제안은 버린다.
      - 남은 제안이 없으면 고정 문구를 쓴다.
      - 이 추가 맥락(사용자 프로젝트 목록)은 **응답 생성에만** 쓰고, 라우팅에는 넣지 않는다.
    - 도메인 버튼(`choices`)은 항상 같이 준다.
    - **연속 HITL 상한:** 같은 대화에서 직전 턴이 이미 HITL이었으면, 이번에도 HITL일 때 제안 없이 `stage=domain`(버튼만)으로 보내 선택을 강제한다. 상한 값은 설정 `max_hitl_streak=1`이다.
  - **BLOCKED:** `ErrorEvent(CONTENT_FILTERED)`를 보내고, `done.status=blocked`로 끝낸다.
- **finalize**
  - `citations.py`: 채널별 답변의 `[N](cite:ID)`를 **화면 표시 순서(채널 순서 → 본문 등장 순서)로 처음 나온 순서대로** 다시 번호를 매긴다. PARALLEL에서 번호가 충돌하지 않게 하기 위해서다.
    - 근거 카드에 없는 ID를 가리키는 마커는 지운다.
    - 카드는 번호 순서로 정렬하고, 인용되지 않은 카드는 뒤에 둔다.
    - 최종 결과는 `DoneEvent.answers`에 담는다.
  - `recommend.py`: task `recommend`로 후속 질문 3개를 만든다(JSON 배열). 실패하면 빈 목록이다.
  - `usage` 합계와 `latency_ms`를 채운다.
- **잘림 감지**
  - 요약과 일반 답변 스트림의 마지막 `finish_reason == "length"`이거나 가드가 `truncated`면 `ChannelDoneEvent(status=truncated)`를 보낸다.
  - 하위 에이전트가 보낸 truncated도 그대로 전달한다.
- **시간 예산:** 턴 전체 `Deadline(turn_budget_s=60)`를 만들고, 라우팅은 `child(routing_budget_s=15)`로 준다. 모든 하위 호출은 이 예산을 넘겨받는다.
- **중단·끊김**
  - 중단 요청은 저장소의 `cancel_requested` 플래그로 받는다. runner는 이벤트마다(최소 0.5초 간격) 이 플래그를 확인한다.
    - 확인되면 하위 스트림을 닫고 `channel_done(stopped)`과 `done(status=stopped)`를 보낸다.
    - 저장소를 공유하는 여러 인스턴스에서도 동작한다.
  - 클라이언트 연결이 끊기면(제너레이터 종료) 턴을 `disconnected`로 기록한다. 이때 `asyncio.shield`로 쓰기를 보호한다.

## 3. 에이전트 클라이언트 (`router_service/agents/`)
- **`AgentClient` Protocol:** `stream(req: AgentRequest, principal: Principal, deadline: Deadline) -> AsyncIterator[Event]`
- **`HttpAgentClient(name, base_url, issuer)`**
  - 호출할 때마다 그 에이전트용 단기 토큰을 발급한다(`aud=<에이전트 이름>`, `service_token_ttl_s`).
  - httpx 스트리밍으로 `POST /v1/stream`을 부르고, `iter_events`로 파싱한다.
  - 연결 실패와 5xx는 `ErrorEvent(AGENT_UNAVAILABLE)`, 403은 `ErrorEvent(FORBIDDEN)`이다.
  - httpx 타임아웃은 connect·write·pool은 유한하게 두고, read는 deadline 잔여로 둔다.
- **`GeneralAgent`** (라우터 안에서 처리)
  - `faq.json`을 `InMemoryIndex`로 검색해 `general.answer` LLM으로 답한다(OutputGuard 적용, 인용 마커).
  - 근거 카드의 source는 `faq`다.
  - `WebSearch` 포트는 fake 구현만 둔다. 데모용 고정 결과를 주고, 결과임을 밝힌다.
- **레지스트리:** `dict[Domain, AgentClient]`. 새 에이전트는 등록만 하면 된다.

## 4. 저장소 (`router_service/store.py`) — SQLite(aiosqlite) 하나
- **테이블**
  - `chats(id, owner, title, created_at, updated_at, deleted)`
  - `turns(id, chat_id, turn_no, status, query, plan_json, payload_json, cancel_requested, deleted, created_at, updated_at, UNIQUE(chat_id, turn_no))`
  - `user_settings(owner, system_prompt)`
- **`begin_turn(chat_id, owner, query)`**
  - 다음 `turn_no`로 `pending` 행을 **턴 시작 시점에** 쓴다.
  - 같은 대화에 `pending` 턴이 있으면 `Conflict`(409)로 거부한다. 동시 요청이 질문·답을 엇갈리게 하는 걸 막는다.
  - 예산 시간이 지난 `pending`은 `error`로 정리한 뒤 진행한다.
- **`finish_turn(turn_id, status, payload)`:** `WHERE status='pending'` 조건부 갱신(compare-and-set)이다. 이미 끝난 턴은 덮지 않는다.
- **조회:** `recent_history(chat_id, limit)`(완료·미삭제 턴만), `hitl_streak(chat_id)`, `request_cancel(turn_id, owner)`, `is_cancel_requested(turn_id)`
- **목록·관리:** `list_chats(owner)`, `get_chat(owner, id)`, `rename`, `soft_delete_chat`, `soft_delete_turn`, 설정 get/put
- **소유자 필터:** 모든 조회에 `owner` 조건을 건다. 남의 대화는 404다.
- **기억은 한 벌만 둔다.** 대화 목록 API와 멀티턴 맥락이 같은 테이블을 읽는다.

## 5. HTTP API (`router_service/api/`)
| 메서드 | 경로 | 설명 |
|---|---|---|
| GET | `/v1/auth/demo-users` | 데모 사용자 목록 (데모 로그인 켜졌을 때만) |
| POST | `/v1/auth/demo-login` | `{user_id}` → `{token, user}` (aud=router) |
| GET | `/v1/me` | 사용자·권한 + 열람 가능 프로젝트(이름 포함) |
| GET·PUT | `/v1/me/settings` | `{system_prompt}` (최대 2,000자) |
| POST | `/v1/chats` | 대화 생성 `{title?}` |
| GET | `/v1/chats` | 내 대화 목록 (최근순) |
| GET | `/v1/chats/{chat_id}` | 대화 + 턴 목록(완료 payload) |
| PATCH | `/v1/chats/{chat_id}` | 이름 변경 |
| DELETE | `/v1/chats/{chat_id}` | 소프트 삭제 |
| POST | `/v1/chats/{chat_id}/turns/stream` | `{query, domain?, project_ids?}` → SSE |
| POST | `/v1/chats/{chat_id}/turns/{turn_id}/regenerate` | → SSE (ReplaySource, 옛 턴 소프트 삭제) |
| POST | `/v1/chats/{chat_id}/turns/{turn_id}/stop` | `{status: "stopping" \| "already_finished"}` |
| GET | `/health/live`, `/health/ready` | ready는 저장소 + 하위 에이전트 `/health/live`(짧은 타임아웃) 확인 |

- **데모 로그인 제한:** 설정 `demo_login_enabled`는 `APP_ENV=prod`에서 켜면 **시작을 거부**한다. 테스트·개발 기능이 운영에 켜진 채로 배포되는 걸 막는다.
- **project_ids:** 요청한 값은 토큰 범위의 부분집합이어야 한다. 아니면 403이다. 비우면 토큰 범위 전체로 본다.
- **CORS:** 설정 `cors_origins` 한 곳에서만 관리한다(기본 `http://localhost:5173`).
- **SSE 이벤트 순서:** `turn` → `progress`(질문 분석) → `route` → 채널별 [`progress`* `references`? `delta`* `channel_done`] → (PARALLEL) `summary` 채널 → `done`
  - HITL: `turn` → `route` → `hitl` → `done(status=hitl, hitl=…)`

## 6. fake 모드 (`router_service/fakes.py`)
키 없이 데모가 자연스럽게 보이도록 task별 핸들러를 등록한다.
- **`route.judge`:** 도메인별 힌트 어휘 목록으로 YES/NO와 근거를 만든다. 힌트는 `judge.yaml` 정의와 맞춘다.
  - 두 도메인 힌트가 모두 강하면 둘 다 YES다.
  - 아무 힌트도 없으면 모두 NO이고, 결과는 HITL이 된다.
- **`route.contextualize`:** "그", "거기", "그 프로젝트", "이어서" 같은 지시어가 있거나 질문이 아주 짧으면 직전 질문의 핵심 명사구를 붙인다. 아니면 원문을 그대로 쓴다.
- **`hitl.suggest`:** 사용자 프로젝트 자리표시자(`{unit:P-101}`)를 쓴 제안 2~3개
- **`summary`:** 채널별 첫 문장들을 묶은 요약
- **`recommend`:** 도메인별 템플릿 후속 질문
- **`general.answer`:** FAQ 상위 근거 문장 + 인용 마커

## 7. 테스트 (네트워크 없음)
- **레이어별 단위 테스트**
  - L1: exact·partial·조사·복합 단서·중복 키워드 거부
  - L2: τ 경계, 두 도메인 동시 통과 시 undecided, 임베더 불일치 시 skipped
  - L3: 규칙(빈 reason, general 제거, 0/1/2 YES), 재시도, BLOCKED
- **캐스케이드 격리:** 레이어를 하나씩 빼도 동작해야 한다.
- **저장소:** CAS, 동시 begin 409, 오래된 pending 정리, 소유자 필터, hitl_streak
- **runner**
  - fake AgentClient로 SINGLE·PARALLEL·HITL·BLOCKED의 이벤트 순서
  - PARALLEL 한쪽 실패 시 요약 생략
  - 중단 플래그로 stopped, 끊김 시 disconnected 기록
  - 인용 재번호, 연속 HITL 강제 선택
- **API:** demo-login → 대화 생성 → SSE 스트림을 끝까지 읽기(하위 에이전트는 fake). 남의 대화 접근 404, project_ids 범위 밖 403, prod+demo 로그인 시작 거부.
