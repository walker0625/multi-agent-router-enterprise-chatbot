"""수행 에이전트 설정.

서비스 고유 값은 `DELIVERY_` 접두어로, 공통 값은 공통 이름(`DATA_DIR`·`LLM_*`·`AUTH_*`)으로 읽는다.
데이터 경로 기본값은 실행 위치가 아니라 패키지 위치에서 계산해, 어디서 띄워도 레포의 `data/` 를 본다.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from chatcore.auth import AuthSettings
from chatcore.llm import LLMSettings

# services/delivery-agent/src/delivery_agent/settings.py → 레포 루트
_REPO_ROOT = Path(__file__).resolve().parents[4]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DELIVERY_", extra="ignore", populate_by_name=True)

    data_dir: Path = Field(default=_REPO_ROOT / "data", validation_alias="DATA_DIR")
    llm: LLMSettings = Field(default_factory=LLMSettings)
    auth: AuthSettings = Field(default_factory=AuthSettings)
