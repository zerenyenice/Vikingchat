"""Per-user OpenViking client factory and URI conventions.

Everything a user stores lands in OpenViking scoped by the ``X-OpenViking-User`` header,
which the SDK derives from the ``user`` argument. Documents are additionally namespaced by
path so that one account can host many users' uploads side by side.
"""

from __future__ import annotations

import re
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from openviking_sdk import AsyncHTTPClient

from .config import Settings

# ``viking://~`` is OpenViking's home alias for the calling user (the ``X-OpenViking-User``
# header). Listings and search results come back in the explicit ``viking://user/<id>/...``
# form, so both spellings must be accepted wherever a client hands a memory URI back to us.
MEMORY_ROOT = "viking://~/memories"
SKILLS_ROOT = "viking://agent/skills"
RESOURCES_ROOT = "viking://resources"


def memory_root(user_id: str) -> str:
    """Explicit (non-alias) memory root for ``user_id``, as OpenViking reports it."""
    return f"viking://user/{user_id}/memories"


def memory_roots(user_id: str) -> tuple[str, str]:
    return MEMORY_ROOT, memory_root(user_id)


def is_memory_uri(uri: str, user_id: str) -> bool:
    """True if ``uri`` is the user's memory root or something underneath it."""
    uri = uri.rstrip("/")
    return any(uri == root or uri.startswith(root + "/") for root in memory_roots(user_id))


def is_memory_root(uri: str, user_id: str) -> bool:
    return uri.rstrip("/") in memory_roots(user_id)


def documents_root(user_id: str) -> str:
    return f"{RESOURCES_ROOT}/users/{user_id}"


def slugify(value: str, *, max_len: int = 64) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9._-]+", "-", value)
    value = re.sub(r"-{2,}", "-", value).strip("-.")
    return (value or "item")[:max_len]


def build_client(settings: Settings, user_id: str) -> AsyncHTTPClient:
    return AsyncHTTPClient(
        url=settings.openviking_url,
        api_key=settings.openviking_api_key,
        account=settings.openviking_account,
        user=user_id,
        agent_id=settings.openviking_agent_id,
        timeout=settings.openviking_timeout,
    )


@asynccontextmanager
async def user_client(settings: Settings, user_id: str) -> AsyncIterator[AsyncHTTPClient]:
    client = build_client(settings, user_id)
    await client.initialize()
    try:
        yield client
    finally:
        try:
            await client.close()
        except Exception:  # pragma: no cover - best effort cleanup
            pass


def connection_kwargs(settings: Settings, user_id: str) -> dict[str, Any]:
    """Keyword arguments understood by the langchain-openviking adapters."""
    return {
        "url": settings.openviking_url,
        "api_key": settings.openviking_api_key,
        "account": settings.openviking_account,
        "user": user_id,
        "actor_peer_id": None,
        "timeout": settings.openviking_timeout,
    }


def flatten_find_result(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Merge the ``memories`` / ``resources`` / ``skills`` buckets of a find() result."""
    kinds = {"memories": "memory", "resources": "resource", "skills": "skill"}
    items: list[dict[str, Any]] = []
    for bucket, kind in kinds.items():
        for entry in result.get(bucket) or []:
            if isinstance(entry, dict):
                items.append({"kind": kind, **entry})
    items.sort(key=lambda item: -(item.get("score") or 0))
    return items
