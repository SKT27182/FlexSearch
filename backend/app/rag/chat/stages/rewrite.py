"""Conversational rewrite, keyword optimize, and clarify stages."""

from __future__ import annotations

from typing import Any, Protocol

from app.prompts import render_prompt
from app.rag.chat.types import ChatTurnMemory
from app.utils.logger import create_logger

logger = create_logger(__name__)

NO_CLARIFY = "NO_CLARIFY"


class SupportsComplete(Protocol):
    async def complete(
        self,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 1024,
        timeout_sec: float = 120.0,
    ) -> Any: ...


def _history_dicts(history: list[ChatTurnMemory]) -> list[dict[str, str]]:
    return [{"role": h.role, "content": h.content} for h in history]


async def rewrite_query(
    llm: SupportsComplete,
    question: str,
    history: list[ChatTurnMemory],
    *,
    temperature: float = 0.2,
) -> str:
    """Rewrite a follow-up into a standalone retrieval query using history."""
    if not history:
        return question
    prompt = render_prompt(
        "rewrite",
        question=question,
        history=_history_dicts(history),
    )
    response = await llm.complete(
        [{"role": "user", "content": prompt}],
        temperature=temperature,
        max_tokens=256,
    )
    rewritten = (response.content or "").strip()
    if not rewritten or rewritten.upper() == "NO_REWRITE":
        return question
    logger.info("Query rewrite: %r → %r", question[:80], rewritten[:80])
    return rewritten


async def optimize_keywords(
    llm: SupportsComplete,
    question: str,
    *,
    temperature: float = 0.1,
) -> str:
    """Extract / expand keywords for lexical-friendly retrieval."""
    prompt = render_prompt("optimize", question=question)
    response = await llm.complete(
        [{"role": "user", "content": prompt}],
        temperature=temperature,
        max_tokens=128,
    )
    optimized = (response.content or "").strip()
    if not optimized or optimized.upper() == "NO_OPTIMIZE":
        return question
    # Prefer appending keywords to preserve original intent
    if optimized.lower() in question.lower():
        return question
    combined = f"{question} {optimized}".strip()
    logger.info("Keyword optimize: %r → %r", question[:80], combined[:80])
    return combined


async def clarify_question(
    llm: SupportsComplete,
    question: str,
    history: list[ChatTurnMemory],
    *,
    temperature: float = 0.2,
) -> str | None:
    """
    Return a clarifying question if the user query is underspecified.

    Returns ``None`` when no clarification is needed.
    """
    prompt = render_prompt(
        "clarify",
        question=question,
        history=_history_dicts(history),
    )
    response = await llm.complete(
        [{"role": "user", "content": prompt}],
        temperature=temperature,
        max_tokens=128,
    )
    text = (response.content or "").strip()
    if not text or NO_CLARIFY in text.upper().replace(" ", ""):
        return None
    if text.upper().strip() == NO_CLARIFY:
        return None
    logger.info("Clarify suggested for question: %r", question[:80])
    return text
