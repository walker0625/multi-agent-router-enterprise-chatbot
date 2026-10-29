"""버전 있는 프롬프트·규칙 파일 로더.

프롬프트와 키워드 같은 판정 재료는 코드가 아니라 YAML 로 둔다. 파일 내용의 해시를
판정 trace 와 평가 리포트에 남겨서, 결과가 바뀌었을 때 프롬프트가 바뀐 건지 모델이
흔들린 건지 구분할 수 있게 한다.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from string import Template
from typing import Any

import yaml


@dataclass(frozen=True)
class VersionedFile:
    path: Path
    data: dict[str, Any]
    digest: str  # 내용 sha256 앞 12자

    @classmethod
    def load(cls, path: Path) -> VersionedFile:
        raw = path.read_bytes()
        data = yaml.safe_load(raw) or {}
        if not isinstance(data, dict) or "version" not in data:
            raise ValueError(f"{path}: 최상위에 version 이 있는 매핑이어야 합니다")
        return cls(path=path, data=data, digest=hashlib.sha256(raw).hexdigest()[:12])

    @property
    def version(self) -> str:
        return str(self.data["version"])

    @property
    def tag(self) -> str:
        return f"{self.path.stem}@{self.version}#{self.digest}"

    def render(self, key: str, **values: Any) -> str:
        """`$name` 자리표시자를 채운다. 빠진 값이 있으면 실패한다(조용히 빈칸으로 두지 않는다)."""
        return Template(self.data[key]).substitute(values)
