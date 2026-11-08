from __future__ import annotations

from fastapi import APIRouter

health_router = APIRouter(prefix="/health", tags=["health"])


@health_router.get("/live")
async def health_live() -> dict[str, str]:
    return {"status": "ok", "mode": "local"}
