"""Test fixtures: app with a fake chat model and a fake OpenViking client."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Iterator
from typing import Any

import pytest

_TMP = tempfile.mkdtemp(prefix="vikingchat-test-")
os.environ.setdefault("DATA_DIR", _TMP)
os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("OPENVIKING_URL", "http://openviking.invalid:1933")
os.environ.setdefault("AGENT_MODEL", "fake:model")

from fastapi.testclient import TestClient  # noqa: E402
from langchain_core.language_models import BaseChatModel  # noqa: E402
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel  # noqa: E402
from langchain_core.messages import AIMessage  # noqa: E402
from langchain_core.runnables import RunnableLambda  # noqa: E402

from app import viking  # noqa: E402
from app.agent.factory import AgentRegistry  # noqa: E402
from app.agent.skill_builder import SkillDraft  # noqa: E402
from app.main import create_app  # noqa: E402


class ToolAwareFakeChatModel(GenericFakeChatModel):
    """GenericFakeChatModel that tolerates bind_tools() so deep agents can wrap it."""

    def bind_tools(self, tools: Any, **kwargs: Any) -> Any:  # type: ignore[override]
        return self


def _replies() -> Iterator[AIMessage]:
    while True:
        yield AIMessage(content="Hello from the fake agent.")


class FakeBuilderModel(BaseChatModel):
    """Model stub whose structured output is a fixed SkillDraft."""

    @property
    def _llm_type(self) -> str:
        return "fake-builder"

    def _generate(self, messages: Any, stop: Any = None, run_manager: Any = None, **kwargs: Any) -> Any:
        raise NotImplementedError

    def with_structured_output(self, schema: Any, **kwargs: Any) -> Any:  # type: ignore[override]
        def _draft(prompt: Any) -> SkillDraft:
            return SkillDraft(
                name="Weekly Report Builder",
                description="Builds the weekly status report. Use when asked for a weekly report.",
                tags=["reporting", "weekly"],
                instructions="## Steps\n1. Collect updates.\n2. Format as bullets.\n",
            )

        return RunnableLambda(_draft)


class FakeVikingClient:
    """Minimal async stand-in for openviking_sdk.AsyncHTTPClient."""

    def __init__(self, *args: Any, user: str | None = None, **kwargs: Any) -> None:
        self.user = user
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.store: dict[str, str] = {
            "viking://user/memories/preferences/style.md": "Prefers concise answers.",
        }
        self.skills: dict[str, str] = {}

    async def initialize(self) -> None:
        self.calls.append(("initialize", {}))

    async def close(self) -> None:
        pass

    async def health(self) -> bool:
        return True

    async def list_skills(self, **kwargs: Any) -> dict[str, Any]:
        return {
            "skills": [
                {"name": name, "description": md.split("description:")[1].split("\n")[0].strip()}
                for name, md in self.skills.items()
            ]
        }

    async def get_skill(self, name: str, **kwargs: Any) -> dict[str, Any]:
        if name not in self.skills:
            raise KeyError(name)
        return {"name": name, "content": self.skills[name]}

    async def delete_skill(self, name: str, **kwargs: Any) -> dict[str, Any]:
        self.skills.pop(name, None)
        return {"status": "ok"}

    async def validate_skill(self, data: str, **kwargs: Any) -> dict[str, Any]:
        return {"valid": data.startswith("---")}

    async def add_skill(self, data: str, **kwargs: Any) -> dict[str, Any]:
        name = data.split("name:")[1].split("\n")[0].strip()
        if name in self.skills:
            raise RuntimeError(f"skill {name} already exists")
        self.skills[name] = data
        self.calls.append(("add_skill", {"name": name}))
        return {"status": "completed", "root_uri": f"viking://agent/skills/{name}"}

    async def update_skill(self, name: str, data: str, **kwargs: Any) -> dict[str, Any]:
        self.skills[name] = data
        self.calls.append(("update_skill", {"name": name}))
        return {"status": "completed", "root_uri": f"viking://agent/skills/{name}"}

    async def add_resource(self, path: str, to: str | None = None, **kwargs: Any) -> dict[str, Any]:
        assert os.path.exists(path), "staged file must exist while add_resource runs"
        self.calls.append(("add_resource", {"path": path, "to": to, **kwargs}))
        self.store[to or path] = f"Resource from {os.path.basename(path)}"
        return {"status": "processing", "task_id": "task-1", "root_uri": to}

    async def get_task(self, task_id: str) -> dict[str, Any]:
        return {"task_id": task_id, "status": "completed"}

    async def overview(self, uri: str) -> str:
        return f"Overview of {uri}"

    async def rm(self, uri: str, **kwargs: Any) -> None:
        self.calls.append(("rm", {"uri": uri}))
        self.store.pop(uri, None)

    async def ls(self, uri: str, **kwargs: Any) -> list[Any]:
        return [{"uri": u, "abstract": c[:40]} for u, c in self.store.items() if u.startswith(uri)]

    async def read(self, uri: str, **kwargs: Any) -> str:
        return self.store[uri]

    async def find(self, query: str = "", target_uri: Any = "", limit: int = 10, **kwargs: Any) -> dict[str, Any]:
        items = [
            {"uri": u, "abstract": c, "score": 0.9}
            for u, c in self.store.items()
            if (not target_uri or u.startswith(target_uri)) and query.lower().split()[0] in c.lower()
        ]
        return {"memories": items, "resources": [], "skills": [], "total": len(items)}

    async def session_exists(self, session_id: str) -> bool:
        return False

    async def delete_session(self, session_id: str) -> None:
        pass


@pytest.fixture
def fake_viking(monkeypatch: pytest.MonkeyPatch) -> FakeVikingClient:
    client = FakeVikingClient()
    monkeypatch.setattr(viking, "build_client", lambda settings, user_id: client)
    return client


@pytest.fixture
def client(fake_viking: FakeVikingClient) -> Iterator[TestClient]:
    def registry_factory(settings: Any, checkpointer: Any) -> AgentRegistry:
        async def skills_loader(settings: Any, user_id: str) -> list[dict[str, Any]]:
            return [{"name": n, "description": ""} for n in fake_viking.skills]

        return AgentRegistry(
            settings,
            checkpointer,
            model_factory=lambda s: ToolAwareFakeChatModel(messages=_replies()),
            tools_factory=lambda s, uid: [],
            middleware_factory=lambda s, uid: [],
            skills_loader=skills_loader,
        )

    app = create_app(agent_registry_factory=registry_factory, builder_model_factory=lambda s: FakeBuilderModel())
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def auth_headers(client: TestClient) -> dict[str, str]:
    username = f"user{os.urandom(3).hex()}"
    resp = client.post("/api/auth/register", json={"username": username, "password": "supersecret1"})
    assert resp.status_code == 201, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def read_sse(response: Any) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in response.iter_lines():
        if line.startswith("data: "):
            events.append(json.loads(line[len("data: "):]))
    return events
