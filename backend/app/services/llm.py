"""
FlexSearch Backend - LLM Service

LiteLLM-based LLM provider abstraction.
"""

import time
from dataclasses import dataclass
from typing import Any, AsyncGenerator

import litellm
from litellm import acompletion

from app.core.config import settings
from app.utils.logger import create_logger

logger = create_logger(__name__)

# Configure LiteLLM
litellm.set_verbose = settings.debug


@dataclass
class LLMResponse:
    """LLM response with usage metadata."""

    content: str
    input_tokens: int
    output_tokens: int
    model: str
    provider: str
    latency_ms: int


@dataclass
class LLMStreamChunk:
    """Streaming chunk from LLM."""

    content: str
    is_final: bool = False
    input_tokens: int | None = None
    output_tokens: int | None = None


class LLMService:
    """LiteLLM-based LLM service supporting multiple providers."""

    def __init__(self) -> None:
        self._model = settings.model_name
        self._api_key = settings.api_key
        self._provider = self._extract_provider(self._model)

    def _extract_provider(self, model: str) -> str:
        """Extract provider from model name."""
        if "/" in model:
            return model.split("/")[0]
        if model.startswith("gpt"):
            return "openai"
        if model.startswith("claude"):
            return "anthropic"
        return "unknown"

    async def complete(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 1024,
    ) -> LLMResponse:
        """
        Generate a completion.

        Args:
            messages: List of message dicts with role and content
            temperature: Sampling temperature
            max_tokens: Maximum tokens to generate

        Returns:
            LLMResponse with content and usage metadata
        """
        start_time = time.time()

        try:
            response = await acompletion(
                model=self._model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                api_key=self._api_key,
            )

            latency_ms = int((time.time() - start_time) * 1000)

            return LLMResponse(
                content=response.choices[0].message.content or "",
                input_tokens=response.usage.prompt_tokens,
                output_tokens=response.usage.completion_tokens,
                model=self._model,
                provider=self._provider,
                latency_ms=latency_ms,
            )
        except Exception as e:
            logger.error(f"LLM completion failed: {e}")
            raise

    async def stream(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 1024,
    ) -> AsyncGenerator[LLMStreamChunk, None]:
        """
        Stream a completion.

        Args:
            messages: List of message dicts with role and content
            temperature: Sampling temperature
            max_tokens: Maximum tokens to generate

        Yields:
            LLMStreamChunk for each token/chunk
        """
        start_time = time.time()
        full_content = ""

        try:
            response = await acompletion(
                model=self._model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                api_key=self._api_key,
                stream=True,
            )

            async for chunk in response:
                delta = chunk.choices[0].delta
                if delta.content:
                    full_content += delta.content
                    yield LLMStreamChunk(
                        content=delta.content,
                        is_final=False,
                    )

            # Final chunk with usage info (estimated if not available)
            latency_ms = int((time.time() - start_time) * 1000)
            yield LLMStreamChunk(
                content="",
                is_final=True,
                input_tokens=len(str(messages)) // 4,  # Rough estimate
                output_tokens=len(full_content) // 4,  # Rough estimate
            )

        except Exception as e:
            logger.error(f"LLM streaming failed: {e}")
            raise

    async def build_rag_prompt(
        self,
        query: str,
        context_chunks: list[dict[str, Any]],
        system_prompt: str | None = None,
    ) -> list[dict[str, str]]:
        """
        Build a RAG prompt with retrieved context.

        Args:
            query: User query
            context_chunks: Retrieved chunks with content and metadata
            system_prompt: Optional system prompt override

        Returns:
            List of messages for completion
        """
        default_system = """You are a helpful assistant that answers questions based on the provided context.
Use the context to answer the user's question accurately. If the context doesn't contain relevant information,
say so clearly. Always cite your sources when possible."""

        context_text = "\n\n".join(
            f"[Source: {chunk.get('metadata', {}).get('filename', 'Unknown')}]\n{chunk.get('content', '')}"
            for chunk in context_chunks
        )

        messages = [
            {"role": "system", "content": system_prompt or default_system},
            {
                "role": "user",
                "content": f"Context:\n{context_text}\n\nQuestion: {query}",
            },
        ]

        return messages

    @property
    def model_name(self) -> str:
        """Get current model name."""
        return self._model

    @property
    def provider(self) -> str:
        """Get current provider."""
        return self._provider


# Singleton instance
_llm_service: LLMService | None = None


def get_llm_service() -> LLMService:
    """Get LLM service singleton."""
    global _llm_service
    if _llm_service is None:
        _llm_service = LLMService()
    return _llm_service
