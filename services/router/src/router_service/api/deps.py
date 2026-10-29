"""FastAPI 의존성 — 실행 객체와 인증된 사용자."""

from __future__ import annotations

from typing import Annotated, cast

from fastapi import Depends, Request

from chatcore.auth import Principal, bearer_token
from chatcore.errors import Forbidden
from router_service.runtime import Runtime


def get_runtime(request: Request) -> Runtime:
    return cast(Runtime, request.app.state.runtime)


RuntimeDep = Annotated[Runtime, Depends(get_runtime)]


def current_principal(rt: RuntimeDep, token: Annotated[str, Depends(bearer_token)]) -> Principal:
    return rt.verifier.verify(token)


PrincipalDep = Annotated[Principal, Depends(current_principal)]


def check_scope(project_ids: list[str], principal: Principal) -> None:
    """요청한 프로젝트 범위는 토큰 범위의 부분집합이어야 한다(비우면 토큰 범위 전체)."""
    outside = set(project_ids) - set(principal.project_ids)
    if outside:
        raise Forbidden(f"project_ids outside token scope: {sorted(outside)}")
