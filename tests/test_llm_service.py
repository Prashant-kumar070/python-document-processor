from types import SimpleNamespace

import httpx
import pytest
from google.genai.errors import APIError
from openai import OpenAIError

from app.core.config import settings
from app.services.llm_service import (
    GROQ_BASE_URL,
    GeminiLLMProvider,
    LLMProviderError,
    LLMService,
    MockLLMProvider,
    OpenAICompatibleLLMProvider,
)


def clear_llm_keys(monkeypatch):
    for name in ("LLM_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.setattr(settings, name, "")


@pytest.mark.parametrize(
    ("provider_name", "expected_type"),
    [
        ("openai", OpenAICompatibleLLMProvider),
        ("groq", OpenAICompatibleLLMProvider),
        ("gemini", GeminiLLMProvider),
        ("mock", MockLLMProvider),
    ],
)
def test_llm_service_selects_provider_from_configuration(
    monkeypatch, provider_name, expected_type
):
    clear_llm_keys(monkeypatch)
    monkeypatch.setattr(settings, "LLM_PROVIDER", provider_name)

    service = LLMService()

    assert isinstance(service.provider, expected_type)


def test_openai_provider_passes_system_instruction_and_messages():
    captured = {}

    class FakeCompletions:
        def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content="Grounded answer.")
                    )
                ]
            )

    client = SimpleNamespace(
        chat=SimpleNamespace(completions=FakeCompletions())
    )
    provider = OpenAICompatibleLLMProvider(
        provider_name="OpenAI",
        api_key="test-key",
        model="gpt-test",
        client=client,
    )

    answer = provider.generate_answer(
        system_instruction="Only use context.",
        messages=[{"role": "user", "content": "Question with context"}],
    )

    assert answer == "Grounded answer."
    assert captured["model"] == "gpt-test"
    assert captured["messages"] == [
        {"role": "system", "content": "Only use context."},
        {"role": "user", "content": "Question with context"},
    ]
    assert captured["temperature"] == 0.1


def test_groq_provider_uses_openai_compatible_endpoint(monkeypatch):
    constructed = {}

    def fake_openai(**kwargs):
        constructed.update(kwargs)
        return object()

    monkeypatch.setattr("app.services.llm_service.OpenAI", fake_openai)
    monkeypatch.setattr(settings, "LLM_PROVIDER", "groq")
    monkeypatch.setattr(settings, "LLM_API_KEY", "shared-test-key")
    monkeypatch.setattr(settings, "LLM_MODEL", "groq-test-model")

    service = LLMService()

    assert isinstance(service.provider, OpenAICompatibleLLMProvider)
    assert constructed == {
        "api_key": "shared-test-key",
        "base_url": GROQ_BASE_URL,
    }
    assert service.provider.model == "groq-test-model"


@pytest.mark.parametrize("provider_name", ["openai", "gemini"])
def test_hosted_providers_use_the_shared_llm_api_key(monkeypatch, provider_name):
    clear_llm_keys(monkeypatch)
    monkeypatch.setattr(settings, "LLM_PROVIDER", provider_name)
    monkeypatch.setattr(settings, "LLM_API_KEY", "shared-test-key")
    monkeypatch.setattr(settings, "LLM_MODEL", "provider-test-model")
    captured = {}

    class FakeClient:
        pass

    def make_client(*, api_key, **kwargs):
        captured["api_key"] = api_key
        captured.update(kwargs)
        return FakeClient()

    if provider_name == "openai":
        monkeypatch.setattr("app.services.llm_service.OpenAI", make_client)
    else:
        monkeypatch.setattr("app.services.llm_service.genai.Client", make_client)

    service = LLMService()

    assert isinstance(service.provider.client, FakeClient)
    assert captured["api_key"] == "shared-test-key"


def test_gemini_provider_sends_system_instruction_and_maps_assistant_role():
    captured = {}

    class FakeModels:
        def generate_content(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(text="Gemini answer.")

    class FakeClient:
        models = FakeModels()

    provider = GeminiLLMProvider(
        api_key="gemini-test-key",
        model="gemini-test-model",
        client=FakeClient(),
    )

    answer = provider.generate_answer(
        system_instruction="Only use retrieved context.",
        messages=[
            {"role": "user", "content": "Previous turn"},
            {"role": "assistant", "content": "Previous answer"},
            {"role": "user", "content": "Current question with context"},
        ],
    )

    assert answer == "Gemini answer."
    assert captured["model"] == "gemini-test-model"
    assert captured["config"].system_instruction == "Only use retrieved context."
    assert [content["role"] for content in captured["contents"]] == [
        "user",
        "model",
        "user",
    ]


def test_gemini_provider_preserves_upstream_quota_error_details():
    response = httpx.Response(
        429,
        request=httpx.Request("POST", "https://generativelanguage.googleapis.com"),
    )
    upstream_error = APIError(
        "429 RESOURCE_EXHAUSTED: quota exceeded",
        response,
        {
            "error": {
                "code": 429,
                "status": "RESOURCE_EXHAUSTED",
                "message": "Quota exceeded for this model.",
            }
        },
    )

    class FakeModels:
        def generate_content(self, **kwargs):
            raise upstream_error

    class FakeClient:
        models = FakeModels()

    provider = GeminiLLMProvider(
        api_key="test-key",
        model="gemini-test-model",
        client=FakeClient(),
    )

    with pytest.raises(LLMProviderError) as exception:
        provider.generate_answer(
            system_instruction="System instruction",
            messages=[{"role": "user", "content": "Question"}],
        )

    detail = exception.value.response_detail()
    assert detail["provider"] == "Gemini"
    assert detail["provider_status"] == 429
    assert detail["provider_code"] == "RESOURCE_EXHAUSTED"
    assert detail["message"] == "Quota exceeded for this model."


def test_openai_compatible_provider_preserves_status_code_and_error_message():
    upstream_error = OpenAIError("upstream request failed")
    upstream_error.status_code = 429
    upstream_error.body = {
        "error": {
            "message": "Rate limit reached for this model.",
            "code": "rate_limit_exceeded",
        }
    }

    class FakeCompletions:
        def create(self, **kwargs):
            raise upstream_error

    client = SimpleNamespace(
        chat=SimpleNamespace(completions=FakeCompletions())
    )
    provider = OpenAICompatibleLLMProvider(
        provider_name="Groq",
        api_key="test-key",
        model="groq-test-model",
        client=client,
    )

    with pytest.raises(LLMProviderError) as exception:
        provider.generate_answer(
            system_instruction="System instruction",
            messages=[{"role": "user", "content": "Question"}],
        )

    detail = exception.value.response_detail()
    assert detail["provider"] == "Groq"
    assert detail["provider_status"] == 429
    assert detail["provider_code"] == "rate_limit_exceeded"
    assert detail["message"] == "Rate limit reached for this model."


def test_provider_error_response_redacts_credentials():
    error = LLMProviderError(
        "Invalid key AIza1234567890123456789012345678901234",
        provider="Gemini",
    )

    detail = error.response_detail()

    assert "AIza" not in detail["message"]
    assert "[REDACTED_API_KEY]" in detail["message"]


def test_mock_provider_requires_no_key_and_returns_explicit_mock_response(
    monkeypatch,
):
    clear_llm_keys(monkeypatch)
    monkeypatch.setattr(settings, "LLM_PROVIDER", "mock")

    answer = LLMService().generate_answer(
        system_instruction="Instruction",
        messages=[{"role": "user", "content": "Question"}],
    )

    assert answer.startswith("Mock chat generation succeeded.")


@pytest.mark.parametrize(
    ("provider_name", "expected_message"),
    [
        ("openai", "OpenAI chat provider"),
        ("gemini", "Gemini chat provider"),
        ("groq", "Groq chat provider"),
    ],
)
def test_hosted_provider_reports_missing_key(monkeypatch, provider_name, expected_message):
    clear_llm_keys(monkeypatch)
    monkeypatch.setattr(settings, "LLM_PROVIDER", provider_name)

    with pytest.raises(LLMProviderError, match=expected_message):
        LLMService().generate_answer(
            system_instruction="Instruction",
            messages=[{"role": "user", "content": "Question"}],
        )


def test_unknown_llm_provider_is_rejected(monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "unknown")

    with pytest.raises(LLMProviderError, match="Supported providers"):
        LLMService()
