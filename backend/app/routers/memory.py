"""Browse and search the user's long-term memory in OpenViking."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from ..config import Settings, get_settings
from ..deps import get_current_user
from ..viking import MEMORY_ROOT, flatten_find_result, user_client

router = APIRouter(prefix="/api/memory", tags=["memory"])


def _assert_memory_uri(uri: str) -> None:
    if not uri.startswith(MEMORY_ROOT):
        raise HTTPException(status_code=400, detail=f"URI must be under {MEMORY_ROOT}")


@router.get("")
async def list_memory(
    query: str | None = Query(default=None, max_length=500),
    limit: int = Query(default=20, ge=1, le=100),
    user: dict[str, Any] = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    try:
        async with user_client(settings, user["id"]) as client:
            if query:
                result = await client.find(query, target_uri=MEMORY_ROOT, limit=limit)
                return {"root": MEMORY_ROOT, "mode": "search", "items": flatten_find_result(result)}
            entries = await client.ls(MEMORY_ROOT, recursive=True, node_limit=limit * 10)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"OpenViking error: {exc}") from exc
    items = []
    for entry in entries or []:
        if isinstance(entry, str):
            items.append({"uri": entry})
        elif isinstance(entry, dict):
            items.append(entry)
    return {"root": MEMORY_ROOT, "mode": "browse", "items": items}


@router.get("/read")
async def read_memory(
    uri: str = Query(..., min_length=len(MEMORY_ROOT)),
    user: dict[str, Any] = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    _assert_memory_uri(uri)
    try:
        async with user_client(settings, user["id"]) as client:
            content = await client.read(uri)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=f"Could not read {uri}: {exc}") from exc
    return {"uri": uri, "content": content}


@router.delete("", status_code=204)
async def delete_memory(
    uri: str = Query(..., min_length=len(MEMORY_ROOT)),
    user: dict[str, Any] = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> None:
    _assert_memory_uri(uri)
    if uri.rstrip("/") == MEMORY_ROOT:
        raise HTTPException(status_code=400, detail="Refusing to delete the memory root")
    try:
        async with user_client(settings, user["id"]) as client:
            await client.rm(uri, recursive=True)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"OpenViking error: {exc}") from exc
