"""데모 사용자·프로젝트·FAQ(읽기 전용 데이터)."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field, TypeAdapter

from chatcore.auth import Principal


class DemoUser(BaseModel):
    sub: str
    name: str
    team: str = ""
    title: str = ""
    description: str = ""
    project_ids: list[str] = Field(default_factory=list)
    sales_units: list[str] = Field(default_factory=list)
    can_read_contacts: bool = False

    def principal(self) -> Principal:
        return Principal.model_validate(self.model_dump(exclude={"title", "description"}))


class Project(BaseModel):
    id: str
    name: str
    account_name: str


class FaqItem(BaseModel):
    id: str
    q: str
    a: str


def load_users(path: Path) -> dict[str, DemoUser]:
    users = TypeAdapter(list[DemoUser]).validate_python(json.loads(path.read_text(encoding="utf-8")))
    return {u.sub: u for u in users}


def load_projects(path: Path) -> dict[str, Project]:
    projects = TypeAdapter(list[Project]).validate_python(json.loads(path.read_text(encoding="utf-8")))
    return {p.id: p for p in projects}


def load_faq(path: Path) -> list[FaqItem]:
    return TypeAdapter(list[FaqItem]).validate_python(json.loads(path.read_text(encoding="utf-8")))
