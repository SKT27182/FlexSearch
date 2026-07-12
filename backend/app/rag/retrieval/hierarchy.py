"""Hierarchical retrieval helpers: summary_level filters + member_chunk expand."""

from __future__ import annotations

from app.rag.retrieval.base import RetrievalResult
from app.schemas.rag_config import HierarchyRetrievalMode
from app.services.search_store import get_search_store
from app.services.search_store.types import SearchFilters, SearchHit, SummaryLevel
from app.utils.logger import create_logger

logger = create_logger(__name__)


def summary_levels_for_mode(mode: HierarchyRetrievalMode) -> list[SummaryLevel] | None:
    """
    Return OpenSearch ``summary_levels`` filter for a hierarchy mode.

    - chunks_only → ["chunk"]
    - summaries_first → ["cluster", "document"]
    - mixed → None (no level filter; search all)
    """
    if mode == "chunks_only":
        return ["chunk"]
    if mode == "summaries_first":
        return ["cluster", "document"]
    return None


def filters_for_hierarchy(
    project_id: str,
    mode: HierarchyRetrievalMode,
    **extra,
) -> SearchFilters:
    levels = summary_levels_for_mode(mode)
    kwargs: dict = {"project_id": project_id, **extra}
    if levels is not None and len(levels) == 1:
        kwargs["summary_level"] = levels[0]
    elif levels is not None:
        kwargs["summary_levels"] = levels
    return SearchFilters(**kwargs)


def hit_to_result(hit: SearchHit) -> RetrievalResult:
    return RetrievalResult(
        content=hit.content,
        score=hit.score,
        document_id=hit.document_id,
        chunk_id=hit.id,
        metadata={
            "filename": hit.filename,
            "chunk_index": hit.chunk_index,
            "summary_level": hit.summary_level,
            "cluster_id": hit.cluster_id,
            "member_chunk_ids": list(hit.member_chunk_ids or []),
            "chunk_type": hit.chunk_type,
            "parent_id": hit.parent_id,
        },
    )


def expand_summary_hits(
    results: list[RetrievalResult],
    *,
    keep_summaries: bool = False,
) -> list[RetrievalResult]:
    """
    Expand cluster/document hits into their member chunks via ``get_by_ids``.

    When ``keep_summaries`` is True (mixed mode), summaries stay and members
    are appended (deduped by chunk_id). Otherwise summaries are replaced.
    """
    member_ids: list[str] = []
    summary_indexes: list[int] = []
    for i, result in enumerate(results):
        level = (result.metadata or {}).get("summary_level", "chunk")
        if level in {"cluster", "document"}:
            ids = list((result.metadata or {}).get("member_chunk_ids") or [])
            if ids:
                summary_indexes.append(i)
                member_ids.extend(ids)

    if not member_ids:
        return results

    store = get_search_store()
    # Preserve order, unique
    seen: set[str] = set()
    ordered_ids: list[str] = []
    for mid in member_ids:
        if mid not in seen:
            seen.add(mid)
            ordered_ids.append(mid)

    members = store.get_by_ids(ordered_ids)
    by_id = {m.id: m for m in members if m is not None}

    if keep_summaries:
        out = list(results)
        existing = {r.chunk_id for r in out}
        for mid in ordered_ids:
            hit = by_id.get(mid)
            if hit is None or mid in existing:
                continue
            out.append(hit_to_result(hit))
            existing.add(mid)
        return out

    # Replace summaries with members; keep non-summary hits
    out: list[RetrievalResult] = []
    existing: set[str] = set()
    summary_set = set(summary_indexes)
    for i, result in enumerate(results):
        if i in summary_set:
            ids = list((result.metadata or {}).get("member_chunk_ids") or [])
            for mid in ids:
                if mid in existing:
                    continue
                hit = by_id.get(mid)
                if hit is None:
                    continue
                member = hit_to_result(hit)
                # Inherit parent summary score as a soft boost signal
                member.score = max(member.score, result.score)
                member.metadata["expanded_from_summary"] = result.chunk_id
                out.append(member)
                existing.add(mid)
        else:
            if result.chunk_id not in existing:
                out.append(result)
                existing.add(result.chunk_id)
    return out


def apply_hierarchy_postprocess(
    results: list[RetrievalResult],
    mode: HierarchyRetrievalMode,
) -> list[RetrievalResult]:
    """Expand summary hits according to retrieval mode."""
    if mode == "chunks_only":
        return results
    if mode == "summaries_first":
        return expand_summary_hits(results, keep_summaries=False)
    # mixed: keep summaries + expand members
    return expand_summary_hits(results, keep_summaries=True)
