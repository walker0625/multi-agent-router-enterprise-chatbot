# 영업 에이전트 명세 — `services/sales-agent`, 패키지 `sales_agent`

계약 이전 영업 자료(고객사·고객 담당자·영업 회의록·회의 첨부)를 **도구 호출 루프**로
검색해 답한다. 공통 규약은 `docs/spec/conventions.md`를 따른다.

## 설계 요점
- **무상태 도구 호출 루프**
  - 호스팅 에이전트(thread/run) 대신 `ChatModel.complete(tools=…)`로 직접 루프를 돈다.
  - 도구 실행기는 요청 컨텍스트(`RequestContext`: principal, 범위 필터, 예산, 메모 캐시)를 **명시적 인자로** 받는다.
  - 그래서 프로세스 공용 슬롯이나 ContextVar로 요청별 권한을 넘기지 않고, 동시 요청끼리 권한이 섞일 수 없다.
- **도구 스키마는 Pydantic 인자 모델에서 생성한다.** 손으로 쓴 JSON과 함수 시그니처를 따로 관리하지 않는다.
- **intent별로 결과를 따로 담는다.** 한 dict에 합치면 뒤 intent의 결과가 앞 결과(예: 날짜 필터를 건 결과)를 조용히 덮어쓴다. 같은 (인덱스, 질의, 필터) 검색은 한 턴 안에서 메모이즈한다.
- **턴 예산**은 deadline, 도구 호출 수 상한, 검색 수 상한 셋으로 둔다.
- **책임별로 모듈을 나눈다.** 검색·권한·마스킹·표시·프롬프트를 한 서비스 클래스에 몰지 않는다.
  - `orchestrator`, `tools`, `retrieval`, `permissions`, `privacy`, `presenters`, `prompts`, `api`

## 1. 데이터와 인덱스 (`indexes.py`)
- **`accounts`**
  - text: 이름·업종·요약·주력 제안·영업 단계
  - fields: `account_id`, `name`, `unit`, `status`, `industry`, `kam`
- **`contacts`**
  - text: 이름·직함·부서·역할·메모(연락처 제외)
  - fields: `account_id`, `name`, `title`, `department`, `role`
  - **전화·메일 원문은 적재할 때 `FieldCipher`로 암호화한 필드(`phone_enc`, `email_enc`)로만 보관한다.** 평문은 버린다.
  - 키는 `SALES_FIELD_KEY_B64`다. 없으면 dev·test에서만 실행 시 무작위 키를 쓰고, prod에서는 시작을 거부한다.
- **`meetings`** (청크 단위)
  - 문서 id: `"{meeting_id}#{seq}"`
  - text: 제목 + 청크
  - fields: `meeting_id`, `account_id`, `unit`, `confidential`, `date`, `kind`, `seq`, `title`, `owner`
- **`meeting_attachments`**
  - fields: `meeting_id`, `account_id`
  - `unit`·`confidential`은 **부모 회의록 값을 물려받는다.**

## 2. 권한 (`permissions.py`)
- **회의록·첨부 필터:** `Or(Eq(confidential, False), And(Eq(confidential, True), In(unit, principal.sales_units)))`
  - `sales_units`가 비면 공개 회의록만 본다(빈 In은 아무것도 허용하지 않는다).
- **고객사·담당자:** 기본 정보는 공개다. 연락처 원문은 `principal.can_read_contacts`일 때만 복호화한다.
  - 권한이 없으면 `[비공개]`로 표시한다.
- 필터는 모두 `chatcore.filters` AST로 만든다.
  - LLM이 준 날짜·이름은 Pydantic으로 파싱된 값으로만 필터에 들어간다.
  - 고객사 이름은 인덱스의 실제 이름과 대조해 `account_id`로 바꾼 뒤 필터에 넣는다. 못 찾으면 필터 없이 질의어로만 검색한다.

## 3. 도구 (`tools.py`)
- **`search_sales_knowledge`** — 인자 모델 `SearchArgs(BaseModel)`
  - `intents: list[Literal["account","contact","meeting"]]`: 1~3개, 중복 제거
  - `query: str`: 1~200자
  - `account_name: str | None`
  - `date_from: date | None`, `date_to: date | None`: 뒤집혀 있으면 서로 바꾼다
  - `top_k: int = 5`: 1~10으로 자른다
  - 스키마는 `SearchArgs.model_json_schema()`로 만든다. `ToolSpec`으로 전달한다.
  - 인자 검증이 실패하면 예외를 올리지 않고 **도구 결과로 오류 설명**을 돌려준다. 모델이 고쳐서 다시 부를 수 있다.
- **실행 규칙**
  - intent별로 검색해 `{intent: [결과…]}`로 따로 담는다.
  - **meeting**
    - 청크를 검색한 뒤 `meeting_id`로 접는다. 회의마다 **점수 상위 2개 청크를 고르고 `seq` 순서로** 정렬하고, 회의 점수는 최대값이다.
    - 이어서 **첨부 2단계 검색**을 한다. 찾은 회의 id로 `In(meeting_id, …)`를 걸고 권한 필터를 같이 건다.
  - **contact:** 화이트리스트 필드만 내보낸다(`chatcore.privacy.pick`). 연락처는 2절 규칙을 따른다.
  - **모든 본문 텍스트**는 결과로 나가기 전에 `redact()`를 거친다. 검색 경계에서, 출처와 상관없이 한 번 적용한다.
  - 도구 결과(모델에 주는 JSON)와 근거 카드(`Reference`)는 같은 결과에서 만든다(`presenters.py`).
    - 카드 id: `account:A-01`, `contact:C-03`, `meeting:M-004`, `attachment:MA-02`
    - 도구 결과에 각 항목의 `cite_id`를 넣어 모델이 인용할 수 있게 한다.

## 4. 오케스트레이터 (`orchestrator.py`)
1. **계획 단계**
   - 메시지 구성: 시스템(지시 + canary + 오늘 날짜) → 사용자 지침(user, 래핑) → history → 질문
   - `complete(tools=[search_sales_knowledge], task="sales.plan")`을 부른다.
   - 도구 호출이 있으면 실행하고 결과를 tool 메시지로 붙인 뒤 다시 부른다.
   - 상한: 라운드 3회, 도구 호출 4회, 검색 8회
     - 상한에 닿으면 남은 도구 호출은 "예산 초과" 결과로 응답하고 답변 단계로 넘어간다.
2. **답변 단계**
   - `stream(task="sales.answer")`로 최종 답을 스트리밍한다.
   - 그 전에 `ReferencesEvent`를 한 번 보낸다. 실제로 쓴 결과만 담는다.
   - `OutputGuard`(최대 6,000자)를 통과시킨다. 잘림이면 `channel_done(truncated)`이다.
- progress: "질문 분석 중"(10) → "영업 자료 검색 중"(40) → "답변 작성 중"(75)
- **시간 예산:** 요청마다 `Deadline(50초)`
- **오류:** 오류 코드 enum과 public 메시지만 내보낸다. SDK 원문은 로그에만 남긴다.

## 5. API (`api.py`)
- `POST /v1/stream`: `TokenVerifier(aud="sales-agent")`
- `GET /health/live`, `/health/ready`
- 클라이언트 연결이 끊기면 진행 중인 호출을 취소한다.

## 6. fake 모드 (`fakes.py`)
- **`sales.plan`**
  - 첫 호출에서 질문의 단서로 `ToolCall`을 만든다.
    - "담당자·연락처·누구" → contact
    - "회의·미팅·협상·견적·제안" → meeting
    - 나머지 → account
  - 고객사 이름은 계정 목록과 대조해 넣는다. 날짜 표현이 있으면 날짜 범위를 넣는다.
  - 도구 결과를 받은 두 번째 호출에서는 도구를 부르지 않는다.
- **`sales.answer`:** 도구 결과 상위 항목의 핵심 문장을 엮고 `[n](cite:cite_id)`를 붙인다. 연락처는 결과에 있는 값(마스킹 또는 원문)을 그대로 쓴다.

## 7. 테스트
- **동시성:** 권한이 다른 두 요청을 `asyncio.gather`로 동시에 돌려도 서로의 비공개 회의록·연락처가 섞이지 않는다.
- **권한:** 비공개 회의록과 그 첨부가 범위 밖 사용자에게 안 나온다. 연락처는 권한이 있으면 원문, 없으면 `[비공개]`다.
- **도구 인자 검증:** 잘못된 인자는 오류 결과로 돌아가고 루프가 계속된다. top_k를 자르고, 날짜를 교정한다.
- **intent별 결과:** 서로 덮어쓰지 않는다. 같은 검색은 메모이즈돼 한 번만 실행된다.
- **예산:** 도구 호출 상한에 닿으면 답변 단계로 넘어간다.
- **청크 접기:** 점수 상위 2개를 고르고 seq 순으로 정렬한다.
- **마스킹:** 회의록 본문 속 전화번호가 도구 결과·카드에 나오지 않는다.
- **API:** aud 불일치 401, SSE 순서
