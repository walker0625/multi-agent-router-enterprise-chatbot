"""헬스 체크. live 는 프로세스만, ready 는 저장소와 하위 에이전트 live 까지 본다."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from router_service.agents import HttpAgentClient
from router_service.api.deps import RuntimeDep

router = APIRouter(tags=["health"])


@router.get("/health/live")
async def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready")
async def ready(rt: RuntimeDep) -> JSONResponse:
    checks: dict[str, str] = {}
    try:
        await rt.store.ping()
        checks["store"] = "ok"
    except Exception:
        checks["store"] = "unavailable"
    remote = {d.value: c for d, c in rt.agents.items() if isinstance(c, HttpAgentClient)}
    results = await asyncio.gather(*(c.live() for c in remote.values()))
    checks.update({name: "ok" if ok else "unavailable" for name, ok in zip(remote, results, strict=True)})
    ok = all(v == "ok" for v in checks.values())
    return JSONResponse(
        {"status": "ok" if ok else "degraded", "checks": checks}, status_code=200 if ok else 503
    )
