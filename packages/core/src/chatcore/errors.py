"""서비스 공통 오류 타입.

공급자(LLM·검색) 오류는 어댑터 안에서 이 타입으로 바꾼다. 위 계층은 오류 문자열을
비교하지 않고 타입과 코드로만 분기한다. 클라이언트에는 `public_message` 만 나가고,
원인(`detail`)은 로그에만 남는다.
"""

from __future__ import annotations

from enum import StrEnum


class ErrorCode(StrEnum):
    INVALID_REQUEST = "INVALID_REQUEST"
    UNAUTHORIZED = "UNAUTHORIZED"
    FORBIDDEN = "FORBIDDEN"
    NOT_FOUND = "NOT_FOUND"
    CONFLICT = "CONFLICT"
    PAYLOAD_TOO_LARGE = "PAYLOAD_TOO_LARGE"
    TIMEOUT = "TIMEOUT"
    CONTENT_FILTERED = "CONTENT_FILTERED"
    OUTPUT_BLOCKED = "OUTPUT_BLOCKED"
    UPSTREAM_ERROR = "UPSTREAM_ERROR"
    AGENT_UNAVAILABLE = "AGENT_UNAVAILABLE"
    SEARCH_FAILED = "SEARCH_FAILED"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    INTERNAL = "INTERNAL"


PUBLIC_MESSAGES: dict[ErrorCode, str] = {
    ErrorCode.INVALID_REQUEST: "요청 형식이 올바르지 않습니다.",
    ErrorCode.UNAUTHORIZED: "인증이 필요합니다.",
    ErrorCode.FORBIDDEN: "접근 권한이 없습니다.",
    ErrorCode.NOT_FOUND: "대상을 찾을 수 없습니다.",
    ErrorCode.CONFLICT: "다른 요청과 충돌했습니다. 잠시 후 다시 시도해 주세요.",
    ErrorCode.PAYLOAD_TOO_LARGE: "요청이 너무 큽니다.",
    ErrorCode.TIMEOUT: "응답 시간이 초과되었습니다. 잠시 후 다시 시도해 주세요.",
    ErrorCode.CONTENT_FILTERED: "안전 정책에 따라 처리할 수 없는 요청입니다.",
    ErrorCode.OUTPUT_BLOCKED: "답변을 안전하게 생성하지 못했습니다. 질문을 바꿔 다시 시도해 주세요.",
    ErrorCode.UPSTREAM_ERROR: "모델 호출 중 오류가 발생했습니다.",
    ErrorCode.AGENT_UNAVAILABLE: "에이전트에 연결할 수 없습니다.",
    ErrorCode.SEARCH_FAILED: "자료 검색에 실패했습니다.",
    ErrorCode.BUDGET_EXCEEDED: "처리 한도를 넘었습니다. 질문 범위를 좁혀 주세요.",
    ErrorCode.INTERNAL: "일시적인 오류가 발생했습니다.",
}

HTTP_STATUS: dict[ErrorCode, int] = {
    ErrorCode.INVALID_REQUEST: 422,
    ErrorCode.UNAUTHORIZED: 401,
    ErrorCode.FORBIDDEN: 403,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.CONFLICT: 409,
    ErrorCode.PAYLOAD_TOO_LARGE: 413,
    ErrorCode.TIMEOUT: 504,
    ErrorCode.CONTENT_FILTERED: 400,
    ErrorCode.OUTPUT_BLOCKED: 502,
    ErrorCode.UPSTREAM_ERROR: 502,
    ErrorCode.AGENT_UNAVAILABLE: 503,
    ErrorCode.SEARCH_FAILED: 502,
    ErrorCode.BUDGET_EXCEEDED: 429,
    ErrorCode.INTERNAL: 500,
}


class AppError(Exception):
    """코드가 붙은 오류. `detail` 은 로그 전용이다."""

    code: ErrorCode = ErrorCode.INTERNAL

    def __init__(self, detail: str = "", *, public_message: str | None = None) -> None:
        super().__init__(detail or self.code.value)
        self.detail = detail
        self.public_message = public_message or PUBLIC_MESSAGES[self.code]

    @property
    def http_status(self) -> int:
        return HTTP_STATUS[self.code]


class InvalidRequest(AppError):
    code = ErrorCode.INVALID_REQUEST


class Unauthorized(AppError):
    code = ErrorCode.UNAUTHORIZED


class Forbidden(AppError):
    code = ErrorCode.FORBIDDEN


class NotFound(AppError):
    code = ErrorCode.NOT_FOUND


class Conflict(AppError):
    code = ErrorCode.CONFLICT


class DeadlineExceeded(AppError):
    code = ErrorCode.TIMEOUT


class ProviderTimeout(AppError):
    code = ErrorCode.TIMEOUT


class ContentFiltered(AppError):
    code = ErrorCode.CONTENT_FILTERED


class OutputBlocked(AppError):
    code = ErrorCode.OUTPUT_BLOCKED


class ProviderError(AppError):
    code = ErrorCode.UPSTREAM_ERROR


class AgentUnavailable(AppError):
    code = ErrorCode.AGENT_UNAVAILABLE


class SearchFailed(AppError):
    code = ErrorCode.SEARCH_FAILED


class BudgetExceeded(AppError):
    code = ErrorCode.BUDGET_EXCEEDED
