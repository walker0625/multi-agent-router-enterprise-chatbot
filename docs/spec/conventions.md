# 공통 구현 규약 (세 서비스 공통)

가상 회사 **누리솔루션**의 사내 업무 챗봇이다. 서비스는 셋이다.

| 서비스 | 디렉토리 | 패키지 | 포트 | 토큰 aud |
|---|---|---|---|---|
| 라우터(수퍼바이저) | `services/router` | `router_service` | 8000 | `router` |
| 수행 에이전트 | `services/delivery-agent` | `delivery_agent` | 8001 | `delivery-agent` |
| 영업 에이전트 | `services/sales-agent` | `sales_agent` | 8002 | `sales-agent` |

## 도메인
- **delivery (프로젝트 수행)** — 계약 이후. 진행 중인 프로젝트의 일정·지연, 이슈·장애, 테스트, 변경 요청(CR), 기성 정산, 인력 투입, 검수, 주간보고, 첨부 산출물.
  - 데이터: `data/delivery/{projects,mails,notes,attachments,glossary}.json`
- **sales (영업)** — 계약 이전. 고객사 현황과 영업 단계, 담당 KAM, 고객 담당자, 영업 회의록, 제안·견적·경쟁사·계약 조건 협의.
  - 데이터: `data/sales/{accounts,contacts,meetings,meeting_attachments}.json`
- **general (일반)** — 챗봇 사용법, 사내 일반 규정(경비·보안 등), 그 밖의 일반 질문. 라우터 안에서 처리한다.
  - 데이터: `data/general/faq.json`
- 데모 사용자와 권한: `data/users.json`
  - `project_ids`: 수행 자료 범위
  - `sales_units`: 비공개 영업 회의록 범위
  - `can_read_contacts`: 연락처 원문 열람

## 코드 구조
- `services/<dir>/pyproject.toml`과 `src/<package>/`, `tests/`로 구성한다. 의존성은 이미 정해 두었으니 **루트 `pyproject.toml`·`uv.lock`은 고치지 않는다.** 꼭 필요한 의존성이 있으면 보고만 한다.
- **앱 팩토리**는 `create_app(settings: Settings | None = None, deps: Deps | None = None) -> FastAPI` 형태로 둔다.
  - `Deps`는 포트(ChatModel·Embedder·SearchIndex·Reranker 등)를 담는 dataclass다. 테스트는 fake를 주입한다.
  - **임포트 시점에 I/O를 하지 않는다.** 모듈 수준 SDK 싱글턴을 두지 않고, 키 조회도 하지 않는다.
  - 데이터 로딩과 인덱스 생성은 FastAPI lifespan 또는 `build_deps(settings)`에서 한다.
- **설정**은 pydantic-settings로 읽는다.
  - 서비스 접두어: `ROUTER_`, `DELIVERY_`, `SALES_`
  - 공통: `LLM_*`(`chatcore.llm.LLMSettings`), `AUTH_*`(`chatcore.auth.AuthSettings`), `APP_ENV`(dev|test|prod)
  - 데이터 경로: `DATA_DIR`. 기본값은 레포 루트의 `data/`이고, 패키지 위치를 기준으로 계산한다.
- **공통 부품은 `chatcore`에서 가져다 쓴다.** 다시 만들지 않는다.
  - 이벤트: `chatcore.events`
  - SSE: `chatcore.sse.event_stream_response`, `iter_events`
  - 오류: `chatcore.errors`
  - 데드라인: `chatcore.deadline.Deadline`
  - 인증: `chatcore.auth`
  - 필터: `chatcore.filters`
  - 검색: `chatcore.search`
  - 가드: `chatcore.guard`
  - 마스킹·암호화: `chatcore.privacy`
  - HTTP 배선: `chatcore.http`
  - 로그: `chatcore.logs`
  - 요청 계약: `chatcore.agent_api.AgentRequest`
  - YAML 로더: `chatcore.prompts.VersionedFile`
- **프롬프트·키워드·규칙 같은 판정 재료**는 서비스 안의 YAML 파일에 둔다(`version` 필드 필수). 파일 태그(`VersionedFile.tag`)를 trace나 로그에 남긴다.
- **LLM 호출**에는 항상 `task="<서비스>.<목적>"` 라벨을 붙인다.
  - fake 모드 응답은 서비스가 `FakeChatModel` 핸들러로 등록한다(`fakes.py`).
  - fake 응답은 **데모가 그럴듯하게 보이도록** 검색 결과를 이용해 만든다. 예: 상위 근거의 핵심 문장 + 인용 마커.
- **스트리밍 답변**은 `chatcore.guard.OutputGuard`(canary + 길이 상한)를 통과시킨다.
  - canary는 시스템 프롬프트에 `canary_instruction()`으로 심는다.
  - 사용자 답변 지침(`system_prompt`)은 `wrap_user_instructions()`로 감싸 **user 메시지**로 넣는다.
- **인용 마커**는 `[N](cite:<reference id>)` 형식이다. N은 1부터 매기고, 근거 카드는 `ReferencesEvent`로 본문보다 먼저 보낸다.
- **오류**는 `AppError` 하위 타입으로 던진다.
  - 스트림 시작 전: HTTP 오류(`install_error_handlers`)
  - 스트림 시작 후: `ErrorEvent(code, message=public_message)`
  - 내부 원인은 로그에만 남긴다.
- **미들웨어**는 `RequestIdMiddleware`와 `BodyLimitMiddleware(max_bytes=64_000)`를 쓴다. 로그는 `configure_logging(service)`로 설정한다.
- **헬스**는 `GET /health/live`(프로세스)와 `GET /health/ready`(의존성·데이터 로딩 상태)로 나눈다.
- **클라이언트 연결이 끊기면** 진행 중인 작업을 취소한다. 제너레이터 `finally`에서 정리하고, 끊긴 뒤에는 이벤트를 만들지 않는다.

## 도메인 에이전트 HTTP 계약 (delivery·sales 공통)
- `POST /v1/stream`
  - 요청: 헤더 `Authorization: Bearer <aud=자기 서비스 토큰>`, 본문 `AgentRequest`
  - 응답: `text/event-stream`
- **이벤트 순서:** `progress`* → `references`(0~1회) → `delta`* → `channel_done`(ok|truncated). 실패하면 중간에 `error` 하나를 보내고 끝낸다.
  - `channel` 필드는 비워 둔다. 라우터가 채운다.
- **권한 위반**(예: 요청한 `project_ids`가 토큰 범위 밖)은 스트림 시작 전에 HTTP 403으로 막는다.
- 서비스는 **대화를 저장하지 않는다.** 직전 대화는 `AgentRequest.history`로 받는다.

## 품질 기준
- **테스트:** `uv run pytest services/<dir>/tests`가 네트워크·API 키 없이 통과해야 한다.
  - 대상: 단위 테스트와, httpx `ASGITransport` 또는 `TestClient`로 SSE를 끝까지 읽는 API 테스트.
  - 테스트용 키쌍은 `chatcore.auth.generate_keypair()`로 만든다.
- **정적 검사:** `uv run ruff check services/<dir>`, `uv run ruff format services/<dir>`, `uv run pyright services/<dir>` 모두 0건이어야 한다.
- **주석은 한국어**로, "무엇을 왜"를 쓴다. 장황한 docstring은 쓰지 않는다.
  - 모듈 docstring에는 설계 이유를 짧게 쓴다(core 모듈 참고).
- 요청받지 않은 기능, 추측성 추상화, 일어날 수 없는 경우의 방어 코드는 넣지 않는다.
