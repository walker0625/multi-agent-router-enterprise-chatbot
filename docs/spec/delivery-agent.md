# 수행 에이전트 명세 — `services/delivery-agent`, 패키지 `delivery_agent`

계약 이후 프로젝트 수행 자료(메일·회의 메모·첨부)를 검색해 근거 있는 답을 스트리밍한다.
**LangGraph 그래프 하나로만** 처리한다. 같은 질문이 실행 경로에 따라 다르게 처리되지 않도록
경로를 하나로 둔다. 공통 규약은 `docs/spec/conventions.md`를 따른다.

## 1. 데이터와 인덱스 (`indexes.py`)
- 시작할 때 `data/delivery/*.json`을 읽어 `chatcore.search.Document`로 만든다. 임베딩을 채워 인덱스 셋을 만든다.
  - `mails`
    - text: `subject + "\n" + body`
    - fields: `project_id`, `date`, `topic`, `sender`, `subject`
  - `notes`
    - text: `title + "\n" + body`
    - fields: `project_id`, `date`, `kind`, `author`, `title`
  - `attachments`
    - text: `filename + "\n" + text`
    - fields: `project_id`, `parent_id`, `parent_type`, `filename`
    - 날짜는 부모 메일에서 가져온다.
- **본문 마스킹은 적재할 때 한 번 한다**(`chatcore.privacy.redact`). 메일 서명의 전화번호·이메일이 인덱스·프롬프트·참조 카드 어디에도 원문으로 남지 않는다.
- 필터 가능 필드는 인덱스마다 명시한다(`filterable=`).

## 2. 그래프 (`graph.py`, LangGraph `StateGraph`)
노드는 모두 `get_stream_writer()`로 이벤트를 흘린다. API는 `graph.astream(..., stream_mode="custom")`을 그대로 SSE로 내보낸다. 이벤트 모델은 `chatcore.events`를 쓴다.

1. **`understand`** — 두 작업을 `asyncio.gather`로 병렬 실행한다.
   - **intent 분류** (`delivery.intent`, JSON 스키마)
     - `intent`: schedule | issue | change | test | cost | staffing | acceptance | status | other
     - `date_from`·`date_to`: 선택. ISO 날짜로 파싱하고, 실패한 값은 버린다.
     - LLM이 실패하면 **키워드 폴백 분류기**를 쓴다.
     - 결과는 (질의 → 결과) **TTL·LRU 캐시**에 둔다.
   - **용어사전 확장** (`glossary.py`)
     - `glossary.json`의 용어가 질의에 나오면 **그 자리 바로 뒤에** 괄호로 별칭을 끼운다. 대소문자·공백은 무시하고, 경계 매칭을 한다.
       - 예: "CR 승인 현황" → "CR(변경요청, 변경 요청서) 승인 현황"
     - `ambiguous_with`가 있는 용어(PO, PM)는 `delivery.glossary_disambiguate` LLM으로 이 질의에서 어느 뜻인지 고른다(타임아웃 3초).
       - 실패하거나 시간이 초과되면 그 용어는 확장하지 않는다.
     - 확장 단계는 **절대 예외를 올리지 않는다.** 실패하면 원문을 쓴다.
     - 결과는 TTL 캐시에 둔다.
   - progress: "질문 분석 중" (10%)
2. **`retrieve`**
   - **범위 필터:** `In("project_id", scope)`
     - `scope`는 요청 `project_ids`다. 비어 있으면 토큰의 `project_ids`다. 부분집합 검사는 API에서 이미 끝났다.
     - scope가 비면 검색하지 않고, "열람 가능한 프로젝트가 없습니다" 안내로 바로 답한다.
     - intent의 날짜가 있으면 `Range("date", …)`를 추가한다(메일·메모).
   - **검색:** **확장한 질의**를 BM25 + 벡터 하이브리드로 세 인덱스에 `asyncio.gather` 병렬로 건다(top 20).
     - 세 인덱스가 모두 실패하면 `SearchFailed`다. 조용히 빈 결과로 넘어가지 않는다.
     - 일부만 실패하면 그 인덱스만 빼고 진행하고, 로그를 남긴다.
   - progress: "프로젝트 자료 검색 중" (35%)
3. **`rerank`**
   - 후보 합집합(최대 30)을 리랭커로 다시 매긴다. 이때 **원문 질의**를 쓴다.
     - 확장 질의는 검색 재현율용이다. 판정은 사용자가 실제로 쓴 표현으로 한다.
   - 상위 8개를 고른다.
   - 리랭크가 실패하면 1차 융합 순서를 그대로 쓴다(fallback).
   - progress: "관련도 정렬 중" (60%)
4. **`grade`**
   - 품질 점수는 상위 리랭크 점수다.
   - 기준(예: 0.34) 미만이고 재시도를 아직 안 했으면 `broaden`으로 간다. `broaden`은 날짜 필터를 빼고 top_k를 2배로 늘려 `retrieve`로 돌아간다.
   - **재시도는 최대 1회다.**
   - 그래도 근거가 없으면 "찾지 못함" 안내로 답한다. 지어내지 않는다.
5. **`synthesize`**
   - `ReferencesEvent`를 먼저 보낸다.
     - 카드 id는 문서 id다.
     - source: mail | note | attachment
     - title: 제목
     - snippet: 앞 160자(마스킹됨)
     - date
     - meta: project_id·project_name
   - 그다음 답변을 스트리밍한다(`delivery.synthesize`).
     - 프롬프트는 `prompts.yaml`에서 읽는다(시스템 지시, canary, 인용 규칙, 근거 목록 `[n] (id) 내용`).
     - 사용자 지침은 `wrap_user_instructions`로 감싸 user 메시지로 넣는다.
     - `AgentRequest.history`는 대화 메시지로 넣는다.
   - `OutputGuard`(최대 6,000자)를 통과시킨다.
     - 마지막 `finish_reason == "length"`이거나 가드가 truncated면 `channel_done(truncated)`, 아니면 `channel_done(ok)`이다.
   - progress: "답변 작성 중" (80%)

- **시간 예산:** 요청마다 `Deadline(50초)`. 노드마다 상한을 `child()`로 준다. 예: 이해 8초, 검색 10초, 리랭크 10초.
- **`ContentFiltered`·`OutputBlocked`·`SearchFailed`·`DeadlineExceeded`**는 `ErrorEvent`(해당 코드, public_message)로 내보내고 끝낸다.

## 3. API (`api.py`)
- `POST /v1/stream`
  - 토큰 검증: `TokenVerifier(aud="delivery-agent")`
  - `AgentRequest.project_ids`가 `principal.project_ids`의 부분집합이 아니면 403이다. 스트림 시작 전에 막는다.
- `GET /health/live`, `/health/ready`: ready는 인덱스 적재 완료 여부다.
- 클라이언트 연결이 끊기면 그래프 실행을 취소한다. 진행 중인 LLM·검색 호출도 함께 끝난다.

## 4. fake 모드 (`fakes.py`)
- **`delivery.intent`:** 키워드 규칙으로 intent를 고르고, "지난달·이번 달·3월" 같은 표현에서 날짜 범위를 만든다.
- **`delivery.glossary_disambiguate`:** 주변 단어로 뜻을 고른다.
  - 예: PO 근처에 발주·구매가 있으면 발주서, 백로그·스프린트가 있으면 Product Owner.
- **`delivery.synthesize`:** 근거 상위 3개의 핵심 문장을 요약형으로 엮고 `[n](cite:id)`를 붙인다.
  - 첫 줄은 질문에 대한 한 문장 결론이다.
- 리랭커는 fake 모드에서 `LexicalReranker`를 쓰고, openai 모드에서 `LLMReranker`를 쓴다.

## 5. 테스트
- **용어사전 확장:** 위치 삽입, 경계("CRM"에는 매칭되지 않음), 모호어 LLM 실패 시 미확장, 예외 미전파
- **intent:** LLM 실패 시 폴백, 날짜 파싱 실패 값 버림
- **retrieve**
  - 범위 밖 프로젝트 문서가 절대 나오지 않음
  - scope가 비면 검색하지 않음
  - 한 인덱스 실패 시 나머지로 진행, 전부 실패 시 `SearchFailed`
- **rerank:** 원문 질의를 받는지 확인한다. 실패하면 1차 순서를 쓴다.
- **grade:** 재시도가 1회만 일어난다.
- **synthesize:** references가 delta보다 먼저 온다. 마스킹이 적용된다. 잘림이면 `channel_done(truncated)`이다.
- **API:** 403(범위 밖), aud가 다른 토큰은 401, SSE를 끝까지 읽어 순서를 확인한다.
