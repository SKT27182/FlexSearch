"""Multi-hop analyze / decompose for complex questions."""

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


def _parse_hops(raw: str, *, max_hops: int, original: str) -> tuple[bool, list[str]]:
    text = raw.strip()
    try:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            data = json.loads(text[start : end + 1])
            if isinstance(data, dict):
                needed = bool(data.get("multihop") or data.get("needed"))
                hops = data.get("hops") or data.get("sub_questions") or []
                if isinstance(hops, list):
                    cleaned = [str(h).strip() for h in hops if str(h).strip()]
                    if not needed or not cleaned:
                        return False, [original]
                    return True, cleaned[:max_hops]
    except json.JSONDecodeError:
        pass

    # Fallback: numbered lines
    hops: list[str] = []
    for line in text.splitlines():
        line = re.sub(r"^\s*[-*\d.)]+\s*", "", line).strip()
        if line and "NO_MULTIHOP" not in line.upper():
            hops.append(line)
    if not hops or "NO_MULTIHOP" in text.upper().replace(" ", ""):
        return False, [original]
    return True, hops[:max_hops]


async def analyze_and_decompose(
    llm: SupportsComplete,
    question: str,
    *,
    max_hops: int = 2,
    temperature: float = 0.2,
    graph_aware: bool = False,
) -> tuple[bool, list[str]]:
    """
    Decide whether multi-hop is needed and return sub-questions.

    Returns ``(needed, hops)``. When not needed, ``hops`` is ``[question]``.
    ``graph_aware`` nudges the prompt toward entity/relation decomposition
    (used for graph-mode projects; retrieval still goes through RAGPipeline).
    """
    prompt = render_prompt(
        "multihop",
        question=question,
        max_hops=max_hops,
        graph_aware=graph_aware,
    )
    response = await llm.complete(
        [{"role": "user", "content": prompt}],
        temperature=temperature,
        max_tokens=512,
    )
    needed, hops = _parse_hops(
        response.content or "", max_hops=max_hops, original=question
    )
    logger.info(
        "Multihop analyze: needed=%s hops=%d graph_aware=%s",
        needed,
        len(hops),
        graph_aware,
    )
    return needed, hops
