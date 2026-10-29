"""데모 로그인·내 정보·답변 지침.

라우터만 토큰을 발급한다. 데모 로그인은 설정으로 켜고 끄며, 운영 환경에서는 켤 수 없다.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from chatcore.auth import Principal
from chatcore.errors import NotFound
from router_service.api.deps import PrincipalDep, RuntimeDep
from router_service.catalog import DemoUser, Project
from router_service.runtime import AUDIENCE

router = APIRouter(prefix="/v1", tags=["auth"])


class DemoLoginBody(BaseModel):
    user_id: str = Field(min_length=1, max_length=64)


class LoginResponse(BaseModel):
    token: str
    user: DemoUser


class MeResponse(Principal):
    projects: list[Project]  # 열람 가능한 프로젝트(이름 포함)


class UserSettings(BaseModel):
    system_prompt: str | None = Field(default=None, max_length=2_000)  # null·빈 문자열 = 지침 없음


@router.get("/auth/demo-users")
async def demo_users(rt: RuntimeDep) -> list[DemoUser]:
    if not rt.settings.demo_login_enabled:
        raise NotFound("demo login disabled")
    return list(rt.users.values())


@router.post("/auth/demo-login")
async def demo_login(body: DemoLoginBody, rt: RuntimeDep) -> LoginResponse:
    user = rt.users.get(body.user_id) if rt.settings.demo_login_enabled else None
    if user is None:
        raise NotFound(f"demo user {body.user_id} not available")
    token = rt.issuer.issue(user.principal(), audience=AUDIENCE, ttl_s=rt.settings.auth.user_token_ttl_s)
    return LoginResponse(token=token, user=user)


@router.get("/me")
async def me(principal: PrincipalDep, rt: RuntimeDep) -> MeResponse:
    projects = [rt.projects[pid] for pid in principal.project_ids if pid in rt.projects]
    return MeResponse(**principal.model_dump(), projects=projects)


@router.get("/me/settings")
async def get_settings(principal: PrincipalDep, rt: RuntimeDep) -> UserSettings:
    return UserSettings(system_prompt=await rt.store.get_settings(principal.sub))


@router.put("/me/settings")
async def put_settings(body: UserSettings, principal: PrincipalDep, rt: RuntimeDep) -> UserSettings:
    system_prompt = (body.system_prompt or "").strip() or None
    await rt.store.put_settings(principal.sub, system_prompt)
    return UserSettings(system_prompt=system_prompt)
