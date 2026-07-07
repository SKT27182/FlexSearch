"""
FlexSearch Backend - LLM Service

LiteLLM-based LLM provider abstraction.
"""

import asyncio
import time
from dataclasses import dataclass

from litellm import acompletion

from app.services.litellm_config import configure_litellm, llm_endpoint
from app.utils.logger import create_logger

logger = create_logger(__name__)


@dataclass
class LLMResponse:
    """LLM response with usage metadata."""

    content: str
    input_tokens: int
    output_tokens: int
    model: str
    provider: str
    latency_ms: int


class LLMService:
    """LiteLLM-based LLM service supporting multiple providers."""

    def __init__(self) -> None:
        configure_litellm()
        ep = llm_endpoint()
        self._model = ep.model
        self._api_key = ep.api_key
        self._api_base = ep.api_base
        self._provider = ep.provider

    async def complete(
        self,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 1024,
        timeout_sec: float = 120.0,
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
            response = await asyncio.wait_for(
                acompletion(
                    model=self._model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    api_key=self._api_key or None,
                    api_base=self._api_base,
                ),
                timeout=timeout_sec,
            )

            latency_ms = int((time.time() - start_time) * 1000)
            logger.verbose(
                "LLM completion: model=%s latency_ms=%d",
                self._model,
                latency_ms,
            )

            return LLMResponse(
                content=response.choices[0].message.content or "",
                input_tokens=response.usage.prompt_tokens,
                output_tokens=response.usage.completion_tokens,
                model=self._model,
                provider=self._provider,
                latency_ms=latency_ms,
            )
        except asyncio.TimeoutError:
            logger.error(
                "LLM completion timed out after %ss (model=%s)",
                timeout_sec,
                self._model,
            )
            raise TimeoutError(
                f"LLM request timed out after {int(timeout_sec)}s"
            ) from None
        except Exception:
            logger.exception("LLM completion failed")
            raise

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
