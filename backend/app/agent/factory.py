"""Build and cache deep agents backed by OpenViking.

One compiled agent is kept per (user, session kind). The agent gets:

* `create_openviking_tools`  - find/search/read/store/add_resource/add_skill tools
* `OpenVikingContextMiddleware` - recalls relevant memory before every model call and
  records each turn into the OpenViking session (which is what feeds memory extraction)
* a LangGraph checkpointer so multi-turn state survives between requests
* a system prompt that lists the user's installed skills for progressive disclosure
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Callable, Sequence

from typing_extensions import NotRequired

from deepagents import DeepAgentState, create_deep_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool
from langchain_openviking import OpenVikingCommitPolicy, OpenVikingContextMiddleware, create_openviking_tools

from ..config import Settings
from ..viking import MEMORY_ROOT, SKILLS_ROOT, connection_kwargs, documents_root, user_client
from .models import build_chat_model
from .prompts import BASE_PROMPT, SKILL_SESSION_ADDENDUM, format_skills_index

log = logging.getLogger(__name__)

class VikingChatState(DeepAgentState):
    """Deep agent state plus the identifiers OpenViking's middleware reads per run."""

    session_id: NotRequired[str]
    peer_id: NotRequired[str]


ModelFactory = Callable[[Settings], BaseChatModel]
ToolsFactory = Callable[[Settings, str], Sequence[BaseTool]]
MiddlewareFactory = Callable[[Settings, str], Sequence[Any]]
SkillsLoader = Callable[[Settings, str], "asyncio.Future[list[dict[str, Any]]] | Any"]


def default_model_factory(settings: Settings) -> BaseChatModel:
    return build_chat_model(settings.agent_model, settings)


def default_tools_factory(settings: Settings, user_id: str) -> Sequence[BaseTool]:
    return create_openviking_tools(
        **connection_kwargs(settings, user_id),
        profile="agent",
        peer_id=user_id,
        allow_forget=settings.allow_forget,
    )


def default_middleware_factory(settings: Settings, user_id: str) -> Sequence[Any]:
    conn = connection_kwargs(settings, user_id)
    conn.pop("timeout", None)  # the middleware manages its own client timeouts
    middleware = OpenVikingContextMiddleware(
        **conn,
        limit=settings.recall_limit,
        token_budget=settings.recall_token_budget,
        peer_id=user_id,
        capture_on_after_agent=True,
        commit_policy=OpenVikingCommitPolicy(
            mode="pending_tokens",
            pending_token_threshold=settings.memory_commit_token_threshold,
        ),
        recall_header="Relevant memory and context recalled from OpenViking:",
    )
    return [middleware]


async def default_skills_loader(settings: Settings, user_id: str) -> list[dict[str, Any]]:
    try:
        async with user_client(settings, user_id) as client:
            result = await client.list_skills()
    except Exception as exc:  # OpenViking unreachable -> agent still works without skills
        log.warning("Could not list skills for %s: %s", user_id, exc)
        return []
    return normalize_skills(result)


def normalize_skills(result: Any) -> list[dict[str, Any]]:
    if isinstance(result, dict):
        skills = result.get("skills")
        if skills is None and "result" in result and isinstance(result["result"], dict):
            skills = result["result"].get("skills")
    else:
        skills = result
    normalized: list[dict[str, Any]] = []
    for entry in skills or []:
        if isinstance(entry, str):
            normalized.append({"name": entry, "description": ""})
        elif isinstance(entry, dict):
            normalized.append(
                {
                    "name": entry.get("name") or entry.get("skill_name") or "",
                    "description": entry.get("description") or entry.get("abstract") or "",
                    "uri": entry.get("uri") or entry.get("root_uri") or "",
                    **{k: v for k, v in entry.items() if k not in ("name", "description", "uri")},
                }
            )
    return [s for s in normalized if s.get("name")]


def build_system_prompt(settings: Settings, username: str, user_id: str, kind: str, skills: list[dict[str, Any]]) -> str:
    prompt = BASE_PROMPT.format(
        app_name=settings.app_name,
        username=username,
        memory_root=MEMORY_ROOT,
        documents_root=documents_root(user_id),
        skills_root=SKILLS_ROOT,
        skills_index=format_skills_index(skills),
    )
    if kind == "skill":
        prompt += SKILL_SESSION_ADDENDUM
    return prompt


@dataclass
class AgentHandle:
    graph: Any
    middleware: Sequence[Any]
    skills: list[dict[str, Any]]

    async def aclose(self) -> None:
        for mw in self.middleware:
            closer = getattr(mw, "aclose", None)
            if closer is None:
                continue
            try:
                await closer()
            except Exception:  # pragma: no cover - best effort
                log.debug("middleware close failed", exc_info=True)


class AgentRegistry:
    """Caches one compiled deep agent per (user_id, session kind)."""

    def __init__(
        self,
        settings: Settings,
        checkpointer: Any,
        *,
        model_factory: ModelFactory = default_model_factory,
        tools_factory: ToolsFactory = default_tools_factory,
        middleware_factory: MiddlewareFactory = default_middleware_factory,
        skills_loader: Any = default_skills_loader,
    ) -> None:
        self.settings = settings
        self.checkpointer = checkpointer
        self.model_factory = model_factory
        self.tools_factory = tools_factory
        self.middleware_factory = middleware_factory
        self.skills_loader = skills_loader
        self._agents: dict[tuple[str, str], AgentHandle] = {}
        self._lock = asyncio.Lock()

    async def get(self, user: dict[str, Any], kind: str) -> AgentHandle:
        key = (user["id"], kind)
        async with self._lock:
            handle = self._agents.get(key)
            if handle is None:
                handle = await self._build(user, kind)
                self._agents[key] = handle
            return handle

    async def invalidate(self, user_id: str) -> None:
        async with self._lock:
            stale = [k for k in self._agents if k[0] == user_id]
            for key in stale:
                handle = self._agents.pop(key)
                await handle.aclose()

    async def aclose(self) -> None:
        async with self._lock:
            for handle in self._agents.values():
                await handle.aclose()
            self._agents.clear()

    async def _build(self, user: dict[str, Any], kind: str) -> AgentHandle:
        skills = await self.skills_loader(self.settings, user["id"])
        model = self.model_factory(self.settings)
        tools = list(self.tools_factory(self.settings, user["id"]))
        middleware = list(self.middleware_factory(self.settings, user["id"]))
        graph = create_deep_agent(
            model=model,
            tools=tools,
            system_prompt=build_system_prompt(self.settings, user["username"], user["id"], kind, skills),
            middleware=middleware,
            state_schema=VikingChatState,
            checkpointer=self.checkpointer,
            name=f"{self.settings.app_name.lower()}-{kind}",
        )
        log.info("Built %s agent for user %s with %d skills", kind, user["username"], len(skills))
        return AgentHandle(graph=graph, middleware=middleware, skills=skills)
