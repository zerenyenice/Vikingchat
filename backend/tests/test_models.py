import pytest

from app.agent.models import build_chat_model
from app.config import Settings


@pytest.fixture
def azure_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "azure-key")
    monkeypatch.delenv("OPENAI_API_VERSION", raising=False)


def test_azure_model_gets_default_api_version(azure_env: None) -> None:
    model = build_chat_model("azure_openai:my-chat-deployment", Settings())
    assert type(model).__name__ == "AzureChatOpenAI"
    assert model.openai_api_version == "2025-01-01-preview"
    assert model.model_name == "my-chat-deployment"
    assert str(model.root_client.base_url).startswith("https://example.openai.azure.com/openai")


def test_azure_model_respects_env_api_version(azure_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_VERSION", "2024-10-21")
    model = build_chat_model("azure_openai:my-chat-deployment", Settings())
    assert model.openai_api_version == "2024-10-21"


def test_non_azure_model_unaffected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    model = build_chat_model("anthropic:claude-opus-5", Settings())
    assert type(model).__name__ == "ChatAnthropic"
