"""Skills stored in OpenViking + the build-skill action for skill sessions."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ..agent.factory import normalize_skills
from ..agent.skill_builder import draft_skill, render_skill_md, store_skill
from ..config import Settings, get_settings
from ..db import Database
from ..deps import get_current_user, get_db
from ..viking import user_client

router = APIRouter(tags=["skills"])


class BuildSkillRequest(BaseModel):
    name: str | None = Field(default=None, max_length=64)
    notes: str | None = Field(default=None, max_length=4000)


@router.get("/api/skills")
async def list_skills(
    user: dict[str, Any] = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> list[dict[str, Any]]:
    try:
        async with user_client(settings, user["id"]) as client:
            result = await client.list_skills()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"OpenViking error: {exc}") from exc
    return normalize_skills(result)


@router.get("/api/skills/{skill_name}")
async def get_skill(
    skill_name: str,
    user: dict[str, Any] = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    try:
        async with user_client(settings, user["id"]) as client:
            skill = await client.get_skill(skill_name, include_content=True)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=f"Skill not found: {exc}") from exc
    return skill if isinstance(skill, dict) else {"name": skill_name, "content": skill}


@router.delete("/api/skills/{skill_name}", status_code=204)
async def delete_skill(
    skill_name: str,
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> None:
    try:
        async with user_client(settings, user["id"]) as client:
            await client.delete_skill(skill_name)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"OpenViking error: {exc}") from exc
    await request.app.state.agents.invalidate(user["id"])


@router.post("/api/sessions/{session_id}/build-skill")
async def build_skill(
    session_id: str,
    body: BuildSkillRequest,
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
    db: Database = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    session = db.get_session(session_id, user["id"])
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session["kind"] != "skill":
        raise HTTPException(status_code=400, detail="Only skill sessions can be built into a skill")
    messages = db.list_messages(session_id)
    if not any(m["role"] == "user" for m in messages):
        raise HTTPException(status_code=400, detail="Chat with the agent about the procedure first")

    model = request.app.state.builder_model_factory(settings)
    try:
        draft = await draft_skill(model, messages, name_hint=body.name, notes=body.notes)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Skill drafting failed: {exc}") from exc

    skill_md = render_skill_md(draft, session_id=session_id, built_by=user["username"])
    try:
        async with user_client(settings, user["id"]) as client:
            stored = await store_skill(client, skill_md, draft.name)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Storing the skill in OpenViking failed: {exc}") from exc

    db.mark_skill_built(session_id, stored["name"], stored["uri"])
    db.add_message(
        session_id,
        "assistant",
        f"Built skill `{stored['name']}` and stored it at `{stored['uri']}`.",
        {"skill_built": {"name": stored["name"], "uri": stored["uri"]}},
    )
    await request.app.state.agents.invalidate(user["id"])
    return {
        "name": stored["name"],
        "uri": stored["uri"],
        "description": draft.description,
        "tags": draft.tags,
        "skill_md": skill_md,
        "session": db.get_session(session_id, user["id"]),
    }
