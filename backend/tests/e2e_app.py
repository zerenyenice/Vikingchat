"""ASGI app for end-to-end UI tests: real API + fake model + fake OpenViking.

Run with:  FRONTEND_DIST=../frontend/dist uvicorn tests.e2e_app:app --port 8011
"""

from __future__ import annotations

import os
import tempfile

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="vikingchat-e2e-"))
os.environ.setdefault("SECRET_KEY", "e2e-secret-key-that-is-long-enough-1234")
os.environ.setdefault("AGENT_MODEL", "fake:model")

from langchain_core.messages import AIMessage  # noqa: E402

from app import viking  # noqa: E402
from app.agent.factory import AgentRegistry  # noqa: E402
from app.main import create_app  # noqa: E402

from .conftest import FakeBuilderModel, FakeVikingClient, ToolAwareFakeChatModel  # noqa: E402

fake_viking = FakeVikingClient()
viking.build_client = lambda settings, user_id: fake_viking  # type: ignore[assignment]


def _replies():
    n = 0
    while True:
        n += 1
        yield AIMessage(content=f"Fake reply #{n}: I searched your **memory** and documents.")


def registry_factory(settings, checkpointer):
    async def skills_loader(settings, user_id):
        return [{"name": name, "description": ""} for name in fake_viking.skills]

    return AgentRegistry(
        settings,
        checkpointer,
        model_factory=lambda s: ToolAwareFakeChatModel(messages=_replies()),
        tools_factory=lambda s, uid: [],
        middleware_factory=lambda s, uid: [],
        skills_loader=skills_loader,
    )


app = create_app(agent_registry_factory=registry_factory, builder_model_factory=lambda s: FakeBuilderModel())
