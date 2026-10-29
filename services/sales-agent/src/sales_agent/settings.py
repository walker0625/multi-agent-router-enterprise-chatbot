"""영업 에이전트 설정.

연락처 암호화 키(`SALES_FIELD_KEY_B64`)가 없으면 dev·test 에서는 실행할 때마다 임시 키를
쓴다 — 데이터는 시작할 때마다 다시 암호화해 적재하므로 키가 바뀌어도 문제없다. prod 에서
키 없이 뜨면 재시작마다 키가 달라져 운영 키 관리가 무의미해지므로 시작을 거부한다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Self

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from chatcore.auth import AuthSettings
from chatcore.llm import LLMSettings

# services/sales-agent/src/sales_agent/settings.py → 레포 루트의 data/
DEFAULT_DATA_DIR = Path(__file__).resolve().parents[4] / "data"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SALES_", extra="ignore", validate_by_name=True)

    app_env: Literal["dev", "test", "prod"] = Field(default="dev", validation_alias="APP_ENV")
    data_dir: Path = Field(default=DEFAULT_DATA_DIR, validation_alias="DATA_DIR")
    field_key_b64: SecretStr | None = None
    llm: LLMSettings = Field(default_factory=LLMSettings)
    auth: AuthSettings = Field(default_factory=AuthSettings)

    @model_validator(mode="after")
    def _require_key_in_prod(self) -> Self:
        if self.app_env == "prod" and self.field_key_b64 is None:
            raise ValueError("APP_ENV=prod 에는 SALES_FIELD_KEY_B64 가 필요합니다")
        return self
