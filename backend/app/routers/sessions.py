"""Chat / skill sessions and the streaming message endpoint."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..agent.chat import stream_turn
from ..config import Settings, get_settings
from ..db import Database
from ..deps import get_current_user, get_db
from ..viking import user_client

router = APIRouter(prefix="/api/sessions", tags=["sessions"])

SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"}


class CreateSessionRequest(BaseModel):
    kind: Literal["chat", "skill"] = "chat"
    title: str | None = Field(default=None, max_length=120)


class RenameRequest(BaseModel):
    title: str = Field(min_length=1, max_length=120)


class MessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=50_000)


def _require_session(db: Database, session_id: str, user: dict[str, Any]) -> dict[str, Any]:
    session = db.get_session(session_id, user["id"])
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


@router.get("")
def list_sessions(user: dict[str, Any] = Depends(get_current_user), db: Database = Depends(get_db)) -> list[dict[str, Any]]:
    return db.list_sessions(user["id"])


@router.post("", status_code=201)
def create_session(
    body: CreateSessionRequest,
    user: dict[str, Any] = Depends(get_current_user),
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    title = body.title or ("New skill session" if body.kind == "skill" else "New chat")
    return db.create_session(user["id"], title, body.kind)


@router.get("/{session_id}")
def get_session(
    session_id: str,
    user: dict[str, Any] = Depends(get_current_user),
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    session = _require_session(db, session_id, user)
    return {**session, "messages": db.list_messages(session_id)}


@router.patch("/{session_id}")
def rename_session(
    session_id: str,
    body: RenameRequest,
    user: dict[str, Any] = Depends(get_current_user),
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    _require_session(db, session_id, user)
    db.rename_session(session_id, body.title)
    return db.get_session(session_id, user["id"]) or {}


@router.delete("/{session_id}", status_code=204)
async def delete_session(
    session_id: str,
    user: dict[str, Any] = Depends(get_current_user),
    db: Database = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> None:
    _require_session(db, session_id, user)
    db.delete_session(session_id)
    try:
        async with user_client(settings, user["id"]) as client:
            if await client.session_exists(session_id):
                await client.delete_session(session_id)
    except Exception:
        # Local record is gone; the OpenViking session (if any) can be cleaned up later.
        pass


@router.post("/{session_id}/messages")
async def send_message(
    session_id: str,
    body: MessageRequest,
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
    db: Database = Depends(get_db),
) -> StreamingResponse:
    session = _require_session(db, session_id, user)
    registry = request.app.state.agents
    handle = await registry.get(user, session["kind"])
    return StreamingResponse(
        stream_turn(handle, db, session, user, body.content),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


@router.post("/{session_id}/commit")
async def commit_session(
    session_id: str,
    user: dict[str, Any] = Depends(get_current_user),
    db: Database = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Ask OpenViking to archive the session now and extract long-term memories from it."""
    _require_session(db, session_id, user)
    try:
        async with user_client(settings, user["id"]) as client:
            if not await client.session_exists(session_id):
                raise HTTPException(status_code=409, detail="No OpenViking session recorded yet for this chat")
            result = await client.commit_session(session_id)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"OpenViking commit failed: {exc}") from exc
    return {"status": "ok", "result": result}


@router.get("/{session_id}/context")
async def session_context(
    session_id: str,
    user: dict[str, Any] = Depends(get_current_user),
    db: Database = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """OpenViking's view of the session: archives, working memory and token estimate."""
    _require_session(db, session_id, user)
    try:
        async with user_client(settings, user["id"]) as client:
            if not await client.session_exists(session_id):
                return {"exists": False}
            context = await client.get_session_context(session_id)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"OpenViking error: {exc}") from exc
    return {"exists": True, **(context or {})}
