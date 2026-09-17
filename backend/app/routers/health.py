from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from ..config import Settings, get_settings
from ..viking import user_client

router = APIRouter(tags=["health"])


@router.get("/api/health")
async def health(settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    openviking: dict[str, Any] = {"url": settings.openviking_url, "healthy": False}
    try:
        async with user_client(settings, "healthcheck") as client:
            openviking["healthy"] = bool(await client.health())
    except Exception as exc:
        openviking["error"] = str(exc)
    return {"status": "ok", "model": settings.agent_model, "openviking": openviking}
