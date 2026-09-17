"""Chat-model construction shared by the agent and the skill builder."""

from __future__ import annotations

import os

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel

from ..config import Settings

AZURE_PROVIDER = "azure_openai"


def build_chat_model(model: str, settings: Settings) -> BaseChatModel:
    """``init_chat_model`` with provider-specific defaults.

    ``azure_openai:<deployment>`` reads ``AZURE_OPENAI_ENDPOINT`` / ``AZURE_OPENAI_API_KEY`` from
    the environment (langchain-openai does that itself) but insists on an API version; when
    ``OPENAI_API_VERSION`` is not set we fall back to ``settings.azure_openai_api_version`` so the
    only Azure-specific variables a deployment needs are the endpoint and the key.
    """
    kwargs: dict[str, object] = {}
    provider, _, _ = model.partition(":")
    if provider == AZURE_PROVIDER and not os.environ.get("OPENAI_API_VERSION"):
        kwargs["api_version"] = settings.azure_openai_api_version
    return init_chat_model(model, **kwargs)
