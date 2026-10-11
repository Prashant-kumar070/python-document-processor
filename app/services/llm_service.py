from abc import ABC, abstractmethod
import re
from typing import Any

from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from openai import OpenAI, OpenAIError

from app.core.config import settings

GROQ_BASE_URL = "https://api.groq.com/openai/v1"


class LLMProviderError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        provider: str,
        provider_status: int | None = None,
        provider_code: str | int | None = None,
    ):
        super().__init__(message)
        self.provider = provider
        self.provider_status = provider_status
        self.provider_code = provider_code

    def response_detail(self) -> dict[str, Any]:
        detail: dict[str, Any] = {
            "error": "LLM provider request failed",
            "provider": self.provider,
            "message": _redact_credentials(str(self)),
        }
        if self.provider_status is not None:
            detail["provider_status"] = self.provider_status
        if self.provider_code is not None:
            detail["provider_code"] = self.provider_code
        return detail


def _redact_credentials(message: str) -> str:
    message = re.sub(r"\bAIza[0-9A-Za-z_-]{20,}\b", "[REDACTED_API_KEY]", message)
    message = re.sub(r"\b(?:sk|gsk)_[A-Za-z0-9_-]{12,}\b", "[REDACTED_API_KEY]", message)
    message = re.sub(
        r"(?i)(authorization\s*:\s*bearer\s+)[^\s,;]+",
        r"\1[REDACTED]",
        message,
    )
    return message[:2000]


def _openai_error_details(exc: OpenAIError) -> tuple[int | None, str | int | None, str]:
    provider_status = getattr(exc, "status_code", None)
    body = getattr(exc, "body", None)
    error_body = body.get("error", body) if isinstance(body, dict) else None
    if isinstance(error_body, dict):
        message = error_body.get("message") or str(exc)
        code = error_body.get("code") or error_body.get("type")
    else:
        message = str(exc)
        code = None
    return provider_status, code, message


class BaseLLMProvider(ABC):
    @abstractmethod
    def generate_answer(
        self,
        *,
        system_instruction: str,
        messages: list[dict[str, str]],
    ) -> str:
        raise NotImplementedError


class OpenAICompatibleLLMProvider(BaseLLMProvider):
    def __init__(
        self,
        *,
        provider_name: str,
        api_key: str | None,
        model: str,
        base_url: str | None = None,
        client=None,
    ):
        self.provider_name = provider_name
        self.model = model
        self.client = client or (
            OpenAI(api_key=api_key, base_url=base_url) if api_key else None
        )

    def generate_answer(
        self,
        *,
        system_instruction: str,
        messages: list[dict[str, str]],
    ) -> str:
        if self.client is None:
            raise LLMProviderError(
                f"An API key is required for the {self.provider_name} chat provider.",
                provider=self.provider_name,
            )
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_instruction},
                    *messages,
                ],
                temperature=0.1,
            )
        except OpenAIError as exc:
            provider_status, provider_code, message = _openai_error_details(exc)
            raise LLMProviderError(
                message,
                provider=self.provider_name,
                provider_status=provider_status,
                provider_code=provider_code,
            ) from exc

        answer = response.choices[0].message.content
        if not answer:
            raise LLMProviderError(
                f"The {self.provider_name} chat provider returned an empty answer.",
                provider=self.provider_name,
            )
        return answer


class GeminiLLMProvider(BaseLLMProvider):
    def __init__(
        self,
        *,
        api_key: str | None,
        model: str,
        client=None,
    ):
        self.model = model
        self.client = client or (genai.Client(api_key=api_key) if api_key else None)

    def generate_answer(
        self,
        *,
        system_instruction: str,
        messages: list[dict[str, str]],
    ) -> str:
        if self.client is None:
            raise LLMProviderError(
                "An API key is required for the Gemini chat provider. "
                "Configure LLM_API_KEY.",
                provider="Gemini",
            )

        contents = [
            {
                "role": "model" if message["role"] == "assistant" else message["role"],
                "parts": [{"text": message["content"]}],
            }
            for message in messages
        ]
        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    temperature=0.1,
                ),
            )
        except genai_errors.APIError as exc:
            error_response = getattr(exc, "response", None)
            if isinstance(error_response, dict):
                error_body = error_response.get("error", error_response)
                message = error_body.get("message") if isinstance(error_body, dict) else None
                provider_status = (
                    error_body.get("code") if isinstance(error_body, dict) else None
                )
                provider_code = (
                    error_body.get("status") if isinstance(error_body, dict) else None
                )
            else:
                message = None
                provider_status = None
                provider_code = None
            message = message or getattr(exc, "message", None) or str(exc)
            raise LLMProviderError(
                message,
                provider="Gemini",
                provider_status=provider_status,
                provider_code=provider_code,
            ) from exc

        answer = getattr(response, "text", None)
        if not answer:
            raise LLMProviderError(
                "The Gemini chat provider returned an empty answer.",
                provider="Gemini",
            )
        return answer


class MockLLMProvider(BaseLLMProvider):
    def generate_answer(
        self,
        *,
        system_instruction: str,
        messages: list[dict[str, str]],
    ) -> str:
        del system_instruction, messages
        return (
            "Mock chat generation succeeded. Configure a hosted LLM provider "
            "to receive generated answers."
        )


class LLMService:
    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        provider: BaseLLMProvider | None = None,
    ):
        self.provider = provider or self._configured_provider(
            api_key=api_key,
            model=model,
        )

    def generate_answer(
        self,
        *,
        system_instruction: str,
        messages: list[dict[str, str]],
    ) -> str:
        return self.provider.generate_answer(
            system_instruction=system_instruction,
            messages=messages,
        )

    @staticmethod
    def _configured_provider(
        *,
        api_key: str | None,
        model: str | None,
    ) -> BaseLLMProvider:
        provider_name = settings.LLM_PROVIDER.strip().lower()
        selected_model = model or settings.LLM_MODEL

        if provider_name == "openai":
            return OpenAICompatibleLLMProvider(
                provider_name="OpenAI",
                api_key=api_key or settings.LLM_API_KEY,
                model=selected_model,
            )
        if provider_name == "groq":
            return OpenAICompatibleLLMProvider(
                provider_name="Groq",
                api_key=api_key or settings.LLM_API_KEY,
                model=selected_model,
                base_url=GROQ_BASE_URL,
            )
        if provider_name == "gemini":
            return GeminiLLMProvider(
                api_key=api_key or settings.LLM_API_KEY,
                model=selected_model,
            )
        if provider_name == "mock":
            return MockLLMProvider()
        raise LLMProviderError(
            f"Unsupported LLM provider '{settings.LLM_PROVIDER}'. "
            "Supported providers: openai, gemini, groq, mock.",
            provider=provider_name or "unknown",
        )
