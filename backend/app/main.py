"""FastAPI application entrypoint."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .agent.factory import AgentRegistry
from .agent.skill_builder import default_builder_model
from .config import get_settings
from .db import Database
from .routers import auth, documents, health, memory, sessions, skills

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
log = logging.getLogger("vikingchat")


@asynccontextmanager
async def _checkpointer(path: str):
    """SQLite checkpointer when available, in-memory otherwise."""
    try:
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    except ImportError:  # pragma: no cover - depends on optional package
        from langgraph.checkpoint.memory import InMemorySaver

        log.warning("langgraph-checkpoint-sqlite not installed; agent state will not persist across restarts")
        yield InMemorySaver()
        return
    async with AsyncSqliteSaver.from_conn_string(path) as saver:
        yield saver


def create_app(*, agent_registry_factory=None, builder_model_factory=default_builder_model) -> FastAPI:
    settings = get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.db = Database(settings.db_path)
        app.state.builder_model_factory = builder_model_factory
        async with _checkpointer(settings.checkpoints_path) as checkpointer:
            if agent_registry_factory is not None:
                app.state.agents = agent_registry_factory(settings, checkpointer)
            else:
                app.state.agents = AgentRegistry(settings, checkpointer)
            try:
                yield
            finally:
                await app.state.agents.aclose()
                app.state.db.close()

    app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    for router in (health.router, auth.router, sessions.router, documents.router, skills.router, memory.router):
        app.include_router(router)

    static_dir = os.environ.get("FRONTEND_DIST", os.path.join(os.path.dirname(__file__), "..", "..", "frontend", "dist"))
    if os.path.isdir(static_dir):
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="frontend")
    return app


app = create_app()
