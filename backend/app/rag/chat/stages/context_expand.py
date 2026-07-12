"""Neighbor context expand via OpenSearch chunk_index range."""

from __future__ import annotations

from typing import Any

from app.rag.retrieval.base import RetrievalResult
from app.services.search_store import get_search_store
from app.services.search_store.types import SearchFilters, SearchHit
from app.utils.logger import create_logger

logger = create_logger(__name__)


def _hit_to_result(hit: SearchHit, *, score: float) -> RetrievalResult:
    return RetrievalResult(
        content=hit.content,
        score=score,
        document_id=hit.document_id,
        chunk_id=hit.id,
        metadata={
            "filename": hit.filename,
            "chunk_index": hit.chunk_index,
            "chunk_type": hit.chunk_type,
            "parent_id": hit.parent_id,
            "summary_level": hit.summary_level,
            "neighbor": True,
        },
    )


async def expand_neighbors(
    results: list[RetrievalResult],
    *,
    project_id: str,
    context_window: int,
    store: Any | None = None,
) -> list[RetrievalResult]:
    """
    Expand each hit with prev/next chunks by ``chunk_index`` within the same document.

    Primary hits keep their scores; neighbors get a small fraction of the primary
    score and are inserted around the primary in document order. Dedupes by chunk_id.
    """
    if context_window <= 0 or not results:
        return results

    search_store = store or get_search_store()
    expanded: list[RetrievalResult] = []
    seen: set[str] = set()

    for primary in results:
        if primary.chunk_id in seen:
            continue
        seen.add(primary.chunk_id)

        meta = primary.metadata or {}
        level = meta.get("summary_level") or "chunk"
        # Summary / cluster hits are not chunk positions — expanding by their
        # chunk_index would pull unrelated neighbors. Member-chunk expand is separate.
        if level != "chunk":
            expanded.append(primary)
            continue

        try:
            chunk_index = int(meta.get("chunk_index", 0))
        except (TypeError, ValueError):
            chunk_index = 0
        document_id = primary.document_id
        if not document_id:
            expanded.append(primary)
            continue

        neighbors: list[RetrievalResult] = []
        try:
            filters = SearchFilters(
                project_id=project_id,
                document_id=document_id,
                summary_level="chunk",
                chunk_index_min=max(0, chunk_index - context_window),
                chunk_index_max=chunk_index + context_window,
            )
            hits, _ = search_store.scroll(filters, size=context_window * 2 + 5)
            for hit in hits:
                if hit.id == primary.chunk_id or hit.id in seen:
                    continue
                # Skip if outside window (defensive)
                if abs(hit.chunk_index - chunk_index) > context_window:
                    continue
                distance = abs(hit.chunk_index - chunk_index)
                neighbor_score = primary.score * (0.35 / max(1, distance))
                neighbors.append(_hit_to_result(hit, score=neighbor_score))
                seen.add(hit.id)
        except Exception as exc:
            logger.warning(
                "Neighbor expand failed for doc=%s idx=%s: %s",
                document_id,
                chunk_index,
                exc,
            )

        # Order: lower chunk_index first among neighbors before primary, then after
        before = sorted(
            [n for n in neighbors if (n.metadata or {}).get("chunk_index", 0) < chunk_index],
            key=lambda r: (r.metadata or {}).get("chunk_index", 0),
        )
        after = sorted(
            [n for n in neighbors if (n.metadata or {}).get("chunk_index", 0) > chunk_index],
            key=lambda r: (r.metadata or {}).get("chunk_index", 0),
        )
        expanded.extend(before)
        expanded.append(primary)
        expanded.extend(after)

    logger.info(
        "Context expand: window=%d in=%d out=%d",
        context_window,
        len(results),
        len(expanded),
    )
    return expanded
