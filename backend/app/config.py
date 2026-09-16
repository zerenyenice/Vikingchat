"""Application settings loaded from environment variables / .env."""

from __future__ import annotations

import os
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "VikingChat"

    # Auth
    secret_key: str = Field(default="change-me-in-production", description="JWT signing key")
    access_token_expire_minutes: int = 60 * 24 * 7
    allow_registration: bool = True

    # Local state (users, sessions, transcripts, agent checkpoints, temp uploads)
    data_dir: str = "./data"
    database_path: str | None = None
    checkpoint_db_path: str | None = None
    upload_dir: str | None = None

    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:3000", "http://localhost:8080"]

    # OpenViking connection. Every request is scoped to the logged-in user via the
    # X-OpenViking-User header that the SDK sets from the `user` argument.
    openviking_url: str = "http://localhost:1933"
    openviking_api_key: str | None = None
    openviking_account: str = "default"
    openviking_agent_id: str = "vikingchat"
    openviking_timeout: float = 120.0

    # LLM used by the deep agent (LangChain `provider:model` string).
    agent_model: str = "anthropic:claude-opus-5"
    skill_builder_model: str | None = None

    # Memory / recall behaviour
    recall_limit: int = 5
    recall_token_budget: int = 32000
    memory_commit_token_threshold: int = 8000
    allow_forget: bool = False

    # Uploads
    max_upload_mb: int = 50

    @property
    def db_path(self) -> str:
        return self.database_path or os.path.join(self.data_dir, "vikingchat.db")

    @property
    def checkpoints_path(self) -> str:
        return self.checkpoint_db_path or os.path.join(self.data_dir, "checkpoints.db")

    @property
    def uploads_path(self) -> str:
        return self.upload_dir or os.path.join(self.data_dir, "uploads")

    @property
    def builder_model(self) -> str:
        return self.skill_builder_model or self.agent_model


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    os.makedirs(settings.data_dir, exist_ok=True)
    os.makedirs(settings.uploads_path, exist_ok=True)
    return settings
