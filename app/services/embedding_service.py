import hashlib
import math
from abc import ABC, abstractmethod

from openai import OpenAI, OpenAIError

from app.core.config import settings
from google import genai
from google.genai import errors as genai_errors
from google.genai import types

VECTOR_DIMENSIONS = settings.EMBEDDING_DIMENSIONS


class EmbeddingProviderError(RuntimeError):
    pass


class BaseEmbeddingProvider(ABC):
    @abstractmethod
    def generate_embedding(self, text: str) -> list[float]:
        raise NotImplementedError

    @abstractmethod
    def generate_embeddings(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError


class MockEmbeddingProvider(BaseEmbeddingProvider):
    def generate_embedding(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        return [
            round(
                (digest[index % len(digest)] / 255.0 + (index * 0.13 % 1)) % 1.0,
                6,
            )
            for index in range(VECTOR_DIMENSIONS)
        ]

    def generate_embeddings(self, texts: list[str]) -> list[list[float]]:
        return [self.generate_embedding(text) for text in texts]


class OpenAIEmbeddingProvider(BaseEmbeddingProvider):
    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        client: OpenAI | None = None,
    ):
        self.api_key = api_key or settings.EMBEDDING_API_KEY or settings.LLM_API_KEY
        self.model = model or settings.EMBEDDING_MODEL
        self.client = client or (OpenAI(api_key=self.api_key) if self.api_key else None)

    def generate_embedding(self, text: str) -> list[float]:
        self._require_client()
        try:
            response = self.client.embeddings.create(model=self.model, input=text)
        except OpenAIError as exc:
            raise EmbeddingProviderError("The embedding provider request failed.") from exc
        return response.data[0].embedding

    def generate_embeddings(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        self._require_client()
        try:
            response = self.client.embeddings.create(model=self.model, input=texts)
        except OpenAIError as exc:
            raise EmbeddingProviderError("The embedding provider request failed.") from exc
        ordered_data = sorted(response.data, key=lambda item: item.index)
        return [item.embedding for item in ordered_data]

    def _require_client(self) -> None:
        if self.client is None:
            raise EmbeddingProviderError(
                "Embedding API credentials are required. Configure EMBEDDING_API_KEY "
                "or LLM_API_KEY, or explicitly select EMBEDDING_PROVIDER=mock for tests."
            )


class GeminiEmbeddingProvider(BaseEmbeddingProvider):
    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        dimensions: int | None = None,
        client=None,
    ):
        self.api_key = api_key or settings.GEMINI_API_KEY or settings.EMBEDDING_API_KEY
        self.model = model or settings.EMBEDDING_MODEL
        self.dimensions = dimensions or settings.EMBEDDING_DIMENSIONS
        self.client = client or (
            genai.Client(api_key=self.api_key) if self.api_key else None
        )

    def generate_embedding(self, text: str) -> list[float]:
        self._require_client()
        try:
            response = self.client.models.embed_content(
                model=self.model,
                contents=text,
                config=types.EmbedContentConfig(
                    task_type="RETRIEVAL_QUERY",
                    output_dimensionality=self.dimensions,
                ),
            )
        except genai_errors.APIError as exc:
            raise EmbeddingProviderError(
                "The Gemini embedding request failed."
            ) from exc
        return self._extract_vectors(response, expected_count=1)[0]

    def generate_embeddings(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        self._require_client()
        try:
            response = self.client.models.embed_content(
                model=self.model,
                contents=texts,
                config=types.EmbedContentConfig(
                    task_type="RETRIEVAL_DOCUMENT",
                    output_dimensionality=self.dimensions,
                ),
            )
        except genai_errors.APIError as exc:
            raise EmbeddingProviderError(
                "The Gemini embedding request failed."
            ) from exc
        return self._extract_vectors(response, expected_count=len(texts))

    def _require_client(self) -> None:
        if self.client is None:
            raise EmbeddingProviderError(
                "Gemini API credentials are required. Configure GEMINI_API_KEY "
                "or EMBEDDING_API_KEY."
            )

    @staticmethod
    def _extract_vectors(response, *, expected_count: int) -> list[list[float]]:
        embeddings = getattr(response, "embeddings", None)
        if embeddings is None and getattr(response, "embedding", None) is not None:
            embeddings = [response.embedding]
        if embeddings is None or len(embeddings) != expected_count:
            received = 0 if embeddings is None else len(embeddings)
            raise EmbeddingProviderError(
                f"Gemini returned {received} embeddings for {expected_count} inputs."
            )

        try:
            return [list(embedding.values) for embedding in embeddings]
        except (AttributeError, TypeError) as exc:
            raise EmbeddingProviderError(
                "Gemini returned an invalid embedding response."
            ) from exc


class EmbeddingService:
    def __init__(self, provider: BaseEmbeddingProvider | None = None):
        self.provider = provider or self._configured_provider()

    def generate_embedding(self, text: str) -> list[float]:
        return self._validate_embedding(self.provider.generate_embedding(text))

    def generate_embeddings(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors = self.provider.generate_embeddings(texts)
        if len(vectors) != len(texts):
            raise EmbeddingProviderError(
                f"Embedding provider returned {len(vectors)} vectors for {len(texts)} texts."
            )
        return [self._validate_embedding(vector) for vector in vectors]

    @staticmethod
    def _configured_provider() -> BaseEmbeddingProvider:
        provider_name = settings.EMBEDDING_PROVIDER.strip().lower()
        if provider_name == "openai":
            return OpenAIEmbeddingProvider()
        if provider_name == "gemini":
            return GeminiEmbeddingProvider()
        if provider_name == "mock":
            return MockEmbeddingProvider()
        raise EmbeddingProviderError(
            f"Unsupported embedding provider '{settings.EMBEDDING_PROVIDER}'. "
            "Supported providers: openai, gemini, mock."
        )

    @staticmethod
    def _validate_embedding(vector: list[float]) -> list[float]:
        if len(vector) != VECTOR_DIMENSIONS:
            raise EmbeddingProviderError(
                f"Expected an embedding with {VECTOR_DIMENSIONS} dimensions; received {len(vector)}."
            )
        if any(not math.isfinite(value) for value in vector):
            raise EmbeddingProviderError("Embedding contains a non-finite value.")
        return vector

    @staticmethod
    def normalize(vector: list[float]) -> list[float]:
        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0:
            return vector
        return [value / norm for value in vector]
