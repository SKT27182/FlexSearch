"""
FlexSearch Backend - LLM Service

LiteLLM-based LLM provider abstraction.
"""

import asyncio
import time
from dataclasses import dataclass
from typing import Any, AsyncGenerator

from app.services.litellm_config import configure_litellm, llm_endpoint
from litellm import acompletion  # after litellm_config installs AWS-preload filter

from app.observability.metrics import metrics
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
        """Generate a completion."""
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
            result = LLMResponse(
                content=response.choices[0].message.content or "",
                input_tokens=response.usage.prompt_tokens,
                output_tokens=response.usage.completion_tokens,
                model=self._model,
                provider=self._provider,
                latency_ms=latency_ms,
            )
            metrics.record_llm(
                kind="complete",
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                seconds=latency_ms / 1000.0,
            )
            return result
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

    async def stream(
        self,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 1024,
        timeout_sec: float = 120.0,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """
        Stream a completion.

        Yields dicts:
          {"type": "token", "content": "..."}
          {"type": "usage", "input_tokens": N, "output_tokens": M, "model": "..."}
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
                    stream=True,
                    stream_options={"include_usage": True},
                ),
                timeout=timeout_sec,
            )
        except asyncio.TimeoutError:
            logger.error(
                "LLM stream timed out after %ss (model=%s)",
                timeout_sec,
                self._model,
            )
            raise TimeoutError(
                f"LLM request timed out after {int(timeout_sec)}s"
            ) from None

        input_tokens = 0
        output_tokens = 0
        try:
            async for chunk in response:
                choice = chunk.choices[0] if chunk.choices else None
                if choice is not None:
                    delta = getattr(choice, "delta", None)
                    text = getattr(delta, "content", None) if delta else None
                    if text:
                        yield {"type": "token", "content": text}
                usage = getattr(chunk, "usage", None)
                if usage is not None:
                    input_tokens = getattr(usage, "prompt_tokens", 0) or 0
                    output_tokens = getattr(usage, "completion_tokens", 0) or 0
            latency_ms = int((time.time() - start_time) * 1000)
            logger.verbose(
                "LLM stream: model=%s latency_ms=%d",
                self._model,
                latency_ms,
            )
            metrics.record_llm(
                kind="stream",
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                seconds=latency_ms / 1000.0,
            )
            yield {
                "type": "usage",
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "model": self._model,
                "latency_ms": latency_ms,
            }
        except Exception:
            logger.exception("LLM stream failed")
            raise

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def provider(self) -> str:
        return self._provider


_llm_service: LLMService | None = None


def get_llm_service() -> LLMService:
    global _llm_service
    if _llm_service is None:
        _llm_service = LLMService()
    return _llm_service
