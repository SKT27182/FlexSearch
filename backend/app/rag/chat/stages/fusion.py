"""Frequency-boost consensus fusion for multi-query / multi-hop results."""

from __future__ import annotations

from app.rag.retrieval.base import RetrievalResult


def frequency_consensus_fuse(
    result_lists: list[list[RetrievalResult]],
    *,
    top_k: int,
    frequency_boost: float = 0.15,
) -> list[RetrievalResult]:
    """
    Fuse multiple retrieval lists by max score + frequency boost.

    Chunks appearing in more lists get ``frequency_boost * (count - 1)`` added
    to their best score, then results are sorted descending and truncated.
    """
    if not result_lists:
        return []
    if len(result_lists) == 1:
        return result_lists[0][:top_k]

    best: dict[str, RetrievalResult] = {}
    counts: dict[str, int] = {}

    for results in result_lists:
        seen_in_list: set[str] = set()
        for result in results:
            key = result.chunk_id
            if key in seen_in_list:
                continue
            seen_in_list.add(key)
            counts[key] = counts.get(key, 0) + 1
            existing = best.get(key)
            if existing is None or result.score > existing.score:
                best[key] = result

    fused: list[RetrievalResult] = []
    for key, result in best.items():
        count = counts.get(key, 1)
        boosted = result.score + frequency_boost * max(0, count - 1)
        meta = dict(result.metadata or {})
        meta["consensus_count"] = count
        meta["consensus_score"] = boosted
        fused.append(
            RetrievalResult(
                content=result.content,
                score=boosted,
                document_id=result.document_id,
                chunk_id=result.chunk_id,
                metadata=meta,
            )
        )

    fused.sort(key=lambda r: r.score, reverse=True)
    return fused[:top_k]
