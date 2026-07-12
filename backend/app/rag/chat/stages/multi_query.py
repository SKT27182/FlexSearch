"""Multi-query generation for consensus retrieval."""

from __future__ import annotations

import json
import re
from typing import Any, Protocol

from app.prompts import render_prompt
from app.utils.logger import create_logger

logger = create_logger(__name__)


class SupportsComplete(Protocol):
    async def complete(
        self,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 1024,
        timeout_sec: float = 120.0,
    ) -> Any: ...


def _parse_query_list(raw: str, *, count: int, original: str) -> list[str]:
    text = raw.strip()
    queries: list[str] = []

    # Try JSON array first
    try:
        start = text.find("[")
        end = text.rfind("]")
        if start != -1 and end != -1 and end > start:
            parsed = json.loads(text[start : end + 1])
            if isinstance(parsed, list):
                queries = [str(q).strip() for q in parsed if str(q).strip()]
    except json.JSONDecodeError:
        pass

    if not queries:
        for line in text.splitlines():
            line = re.sub(r"^\s*[-*\d.)]+\s*", "", line).strip()
            if line:
                queries.append(line)

    # Always include original; dedupe case-insensitively
    out: list[str] = []
    seen: set[str] = set()
    for q in [original, *queries]:
        key = q.lower()
        if key in seen or not q:
            continue
        seen.add(key)
        out.append(q)
        if len(out) >= count:
            break
    if original not in out and len(out) < count:
        out.insert(0, original)
    return out[:count] if out else [original]


async def generate_multi_queries(
    llm: SupportsComplete,
    question: str,
    *,
    count: int = 3,
    temperature: float = 0.4,
) -> list[str]:
    """Generate ``count`` retrieval query variants (includes the original)."""
    if count <= 1:
        return [question]
    prompt = render_prompt("multi_query", question=question, count=count)
    response = await llm.complete(
        [{"role": "user", "content": prompt}],
        temperature=temperature,
        max_tokens=512,
    )
    queries = _parse_query_list(response.content or "", count=count, original=question)
    logger.info("Multi-query generated %d variants for %r", len(queries), question[:60])
    return queries
