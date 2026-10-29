"""라우터 설정.

경로 기본값은 패키지 위치에서 계산한다. 어느 작업 디렉토리에서 띄워도 같은 데이터와
판정 재료(YAML)를 읽는다. 데모 로그인은 운영 환경에서 켜진 채로 뜨지 못하게 설정 단계에서
막는다 — 테스트용 기능이 운영에 배포되는 사고를 시작 시점에 드러낸다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Self

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from chatcore.auth import AuthSettings
from chatcore.llm import LLMSettings

SERVICE_DIR = Path(__file__).resolve().parents[2]  # services/router
REPO_ROOT = SERVICE_DIR.parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ROUTER_", extra="ignore")

    app_env: Literal["dev", "test", "prod"] = Field(
        default="dev", validation_alias=AliasChoices("APP_ENV", "app_env")
    )
    data_dir: Path = Field(default=REPO_ROOT / "data", validation_alias=AliasChoices("DATA_DIR", "data_dir"))
    routing_dir: Path = SERVICE_DIR / "routing"
    prompts_path: Path = SERVICE_DIR / "prompts.yaml"
    db_path: str = ".run/router.db"

    demo_login_enabled: bool = True
    cors_origins: list[str] = ["http://localhost:5173"]
    delivery_url: str = "http://localhost:8001"
    sales_url: str = "http://localhost:8002"

    turn_budget_s: float = 60.0
    routing_budget_s: float = 15.0
    max_hitl_streak: int = 1
    history_turns: int = 3  # 에이전트에 넘기는 직전 완료 턴 수(입구 병합은 이 중 최근 2개만 쓴다)
    parallel_queue_size: int = 64
    cancel_poll_s: float = 0.5
    answer_max_chars: int = 4_000  # 일반 답변·요약 출력 상한(OutputGuard)
    faq_min_overlap: float = 0.4  # FAQ 근거로 인정하는 질의 토큰 겹침 비율. 못 넘으면 웹 검색(데모)

    llm: LLMSettings = Field(default_factory=LLMSettings)
    auth: AuthSettings = Field(default_factory=AuthSettings)

    @model_validator(mode="after")
    def _prod_guards(self) -> Self:
        if self.app_env == "prod" and self.demo_login_enabled:
            raise ValueError(
                "APP_ENV=prod 에서는 데모 로그인을 켤 수 없습니다(ROUTER_DEMO_LOGIN_ENABLED=false)"
            )
        if self.app_env == "prod" and self.auth.private_key_path is None:
            raise ValueError(
                "APP_ENV=prod 에서는 AUTH_PRIVATE_KEY_PATH 가 필요합니다(개발용 키를 만들지 않는다)"
            )
        return self

    @property
    def private_key_path(self) -> Path:
        # dev·test 에서 경로를 안 주면 개발용 키 위치를 쓴다(없으면 시작할 때 만든다).
        return self.auth.private_key_path or Path(".devkeys/jwt_private.pem")
