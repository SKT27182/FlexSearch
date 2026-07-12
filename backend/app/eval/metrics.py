"""Eval metric helpers: retrieval@k and lexical faithfulness."""

from __future__ import annotations

import re
from typing import Sequence


_TOKEN_RE = re.compile(r"[a-z0-9]+", re.IGNORECASE)


def tokenize(text: str) -> set[str]:
    return {t.lower() for t in _TOKEN_RE.findall(text or "")}


def retrieval_at_k(
    retrieved_ids: Sequence[str],
    relevant_ids: Sequence[str],
    k: int,
) -> dict[str, float]:
    """
    Compute hit@k, recall@k, and precision@k for one query.

    ``retrieved_ids`` are ordered by rank (best first).
    """
    if k <= 0:
        return {"hit_at_k": 0.0, "recall_at_k": 0.0, "precision_at_k": 0.0}
    top = list(retrieved_ids)[:k]
    relevant = {str(r) for r in relevant_ids if r}
    if not relevant:
        return {"hit_at_k": 0.0, "recall_at_k": 0.0, "precision_at_k": 0.0}
    hits = sum(1 for rid in top if str(rid) in relevant)
    hit_at_k = 1.0 if hits > 0 else 0.0
    recall_at_k = hits / len(relevant)
    precision_at_k = hits / len(top) if top else 0.0
    return {
        "hit_at_k": hit_at_k,
        "recall_at_k": recall_at_k,
        "precision_at_k": precision_at_k,
    }


def faithfulness_score(answer: str, contexts: Sequence[str]) -> float:
    """
    Lexical faithfulness proxy: fraction of answer tokens that appear in context.

    Not a substitute for LLM-as-judge, but CI-friendly and deterministic.
    Returns 1.0 for empty answers; 0.0 when answer has tokens but no context.
    """
    answer_tokens = tokenize(answer)
    if not answer_tokens:
        return 1.0
    context_tokens: set[str] = set()
    for ctx in contexts:
        context_tokens |= tokenize(ctx)
    if not context_tokens:
        return 0.0
    supported = sum(1 for t in answer_tokens if t in context_tokens)
    return supported / len(answer_tokens)


def mean(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)
