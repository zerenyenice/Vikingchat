"""Turn a skill-creation session into a SKILL.md stored in OpenViking."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

import yaml
from langchain_core.language_models import BaseChatModel
from pydantic import BaseModel, Field

from ..config import Settings
from ..viking import SKILLS_ROOT
from .models import build_chat_model
from .prompts import SKILL_BUILDER_PROMPT

log = logging.getLogger(__name__)

SKILL_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class SkillDraft(BaseModel):
    """Structured output requested from the model."""

    name: str = Field(description="kebab-case identifier, max 64 chars")
    description: str = Field(description="what the skill does and when to use it")
    tags: list[str] = Field(default_factory=list)
    instructions: str = Field(description="Markdown body of SKILL.md")


def normalize_skill_name(raw: str) -> str:
    name = raw.strip().lower()
    name = re.sub(r"[^a-z0-9]+", "-", name).strip("-")
    name = re.sub(r"-{2,}", "-", name)[:64].strip("-")
    if not name or not SKILL_NAME_RE.match(name):
        raise ValueError(f"Could not derive a valid skill name from {raw!r}")
    return name


def render_transcript(messages: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for message in messages:
        role = message.get("role", "user")
        content = (message.get("content") or "").strip()
        if not content:
            continue
        label = {"user": "User", "assistant": "Assistant"}.get(role, role.title())
        lines.append(f"{label}: {content}")
        tool_calls = (message.get("meta") or {}).get("tool_calls") or []
        for call in tool_calls:
            lines.append(f"  [assistant used tool {call.get('name')}]")
    return "\n\n".join(lines)


def render_skill_md(draft: SkillDraft, *, session_id: str, built_by: str) -> str:
    frontmatter: dict[str, Any] = {
        "name": draft.name,
        "description": " ".join(draft.description.split()),
    }
    tags = [t.strip().lower() for t in draft.tags if t and t.strip()]
    if tags:
        frontmatter["tags"] = tags[:8]
    frontmatter["metadata"] = {
        "source": "vikingchat",
        "session_id": session_id,
        "built_by": built_by,
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    yaml_text = yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=True).strip()
    body = draft.instructions.strip()
    if not body.startswith("#"):
        body = f"# {draft.name}\n\n{body}"
    return f"---\n{yaml_text}\n---\n\n{body}\n"


def default_builder_model(settings: Settings) -> BaseChatModel:
    return build_chat_model(settings.builder_model, settings)


async def draft_skill(
    model: BaseChatModel,
    messages: list[dict[str, Any]],
    *,
    name_hint: str | None = None,
    notes: str | None = None,
) -> SkillDraft:
    hints = []
    if name_hint:
        hints.append(f"- The user wants the skill to be named `{name_hint}` (normalise to kebab-case).")
    if notes:
        hints.append(f"- Extra guidance from the user: {notes}")
    prompt = SKILL_BUILDER_PROMPT.format(
        hints=("\nAdditional requirements:\n" + "\n".join(hints) + "\n") if hints else "",
        transcript=render_transcript(messages),
    )
    structured = model.with_structured_output(SkillDraft)
    draft = await structured.ainvoke(prompt)
    if not isinstance(draft, SkillDraft):
        draft = SkillDraft.model_validate(draft)
    draft.name = normalize_skill_name(name_hint or draft.name)
    return draft


async def store_skill(client: Any, skill_md: str, name: str, *, replace: bool = True) -> dict[str, Any]:
    """Validate and add (or update) the skill in OpenViking. Returns {name, uri, result}."""
    try:
        validation = await client.validate_skill(data=skill_md)
    except Exception as exc:  # validation endpoint is advisory
        log.debug("validate_skill unavailable: %s", exc)
        validation = None
    if isinstance(validation, dict) and validation.get("valid") is False:
        raise ValueError(f"Generated SKILL.md is invalid: {validation.get('errors') or validation}")

    try:
        result = await client.add_skill(skill_md, wait=True, timeout=180)
    except Exception as exc:
        if not replace or "exist" not in str(exc).lower() and "conflict" not in str(exc).lower():
            raise
        log.info("Skill %s exists, updating instead", name)
        result = await client.update_skill(name, skill_md, wait=True, timeout=180)
    uri = ""
    if isinstance(result, dict):
        uri = result.get("uri") or result.get("root_uri") or result.get("skill_uri") or ""
    if not uri:
        uri = f"{SKILLS_ROOT}/{name}"
    return {"name": name, "uri": uri, "result": result}
