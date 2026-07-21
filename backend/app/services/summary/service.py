"""Hierarchical summary service: K-Means → Tier-1 clusters → Tier-2 manifesto."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any
from uuid import NAMESPACE_DNS, uuid5

import numpy as np
from sklearn.cluster import KMeans

from app.prompts import render_prompt
from app.schemas.rag_config import HierarchicalSummaryConfig
from app.services.embedding import get_embedding_service
from app.services.llm import get_llm_service
from app.services.search_store import get_search_store
from app.services.search_store.types import SearchDocument, SearchFilters, SearchHit
from app.utils.logger import create_logger

logger = create_logger(__name__)


@dataclass
class SummaryJobResult:
    document_id: str
    project_id: str
    cluster_count: int
    manifesto_id: str | None
    skipped: bool = False
    reason: str | None = None


def _stable_summary_id(document_id: str, generation: int, level: str, key: str) -> str:
    return str(
        uuid5(NAMESPACE_DNS, f"summary:{document_id}:{generation}:{level}:{key}")
    )


def _auto_n_clusters(n_chunks: int, configured: int | None) -> int:
    if configured is not None:
        return max(2, min(configured, n_chunks))
    # ≈ sqrt(n), clamped
    return max(2, min(int(math.sqrt(n_chunks)), n_chunks, 50))


def _scroll_all(filters: SearchFilters, *, page_size: int = 500) -> list[SearchHit]:
    store = get_search_store()
    all_hits: list[SearchHit] = []
    search_after = None
    while True:
        hits, search_after = store.scroll(
            filters=filters, size=page_size, search_after=search_after
        )
        all_hits.extend(hits)
        if not hits or search_after is None:
            break
        if len(hits) < page_size:
            break
    return all_hits


def _delete_existing_summaries(document_id: str, generation: int) -> None:
    """Remove prior cluster/document summaries for this document (keep chunks)."""
    store = get_search_store()
    ids: list[str] = []
    for level in ("cluster", "document"):
        hits = _scroll_all(
            SearchFilters(
                document_id=document_id,
                rag_generation=generation,
                summary_level=level,  # type: ignore[arg-type]
            )
        )
        ids.extend(h.id for h in hits)
    if ids:
        store.delete_by_ids(ids)


async def build_document_summaries(
    *,
    project_id: str,
    document_id: str,
    generation: int,
    filename: str,
    config: HierarchicalSummaryConfig,
) -> SummaryJobResult:
    """
    Cluster chunk embeddings → Tier-1 cluster summaries → Tier-2 manifesto.

    Upserts OpenSearch docs with ``summary_level`` cluster|document and
    ``member_chunk_ids``.
    """
    if not config.enabled:
        return SummaryJobResult(
            document_id=document_id,
            project_id=project_id,
            cluster_count=0,
            manifesto_id=None,
            skipped=True,
            reason="summaries.disabled",
        )

    store = get_search_store()
    chunks = _scroll_all(
        SearchFilters(
            project_id=project_id,
            document_id=document_id,
            rag_generation=generation,
            summary_level="chunk",
        ),
        page_size=1000,
    )
    # Prefer non-parent chunks for clustering when parent_child is used
    chunk_hits = [h for h in chunks if h.chunk_type != "parent"] or list(chunks)

    if len(chunk_hits) < config.min_chunks:
        return SummaryJobResult(
            document_id=document_id,
            project_id=project_id,
            cluster_count=0,
            manifesto_id=None,
            skipped=True,
            reason=f"too_few_chunks:{len(chunk_hits)}",
        )

    embedding = get_embedding_service()
    # Prefer stored embeddings from scroll payload when present
    vectors: list[list[float]] = []
    for hit in chunk_hits:
        emb = hit.payload.get("embedding") if hit.payload else None
        if isinstance(emb, list) and emb:
            vectors.append(emb)
        else:
            vectors.append(embedding.embed(hit.content))

    X = np.asarray(vectors, dtype=np.float32)
    n_clusters = _auto_n_clusters(len(chunk_hits), config.n_clusters)
    logger.info(
        "Summarizing document=%s chunks=%d clusters=%d",
        document_id,
        len(chunk_hits),
        n_clusters,
    )

    km = KMeans(n_clusters=n_clusters, n_init=10, random_state=42)
    labels = km.fit_predict(X)

    _delete_existing_summaries(document_id, generation)

    llm = get_llm_service()
    cluster_docs: list[SearchDocument] = []
    cluster_summaries: list[str] = []

    for cluster_idx in range(n_clusters):
        member_hits = [
            chunk_hits[i] for i, lab in enumerate(labels) if int(lab) == cluster_idx
        ]
        if not member_hits:
            continue
        member_ids = [h.id for h in member_hits]
        excerpts = "\n\n".join(
            f"[{i + 1}] {h.content[:1200]}" for i, h in enumerate(member_hits[:20])
        )
        prompt = render_prompt(
            "cluster_summary",
            filename=filename,
            cluster_index=cluster_idx,
            excerpts=excerpts,
        )
        response = await llm.complete(
            messages=[
                {
                    "role": "system",
                    "content": "You write concise factual cluster summaries for RAG.",
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
            max_tokens=config.cluster_max_tokens,
        )
        summary_text = (response.content or "").strip()
        if not summary_text:
            summary_text = member_hits[0].content[:500]
        cluster_summaries.append(summary_text)

        # Centroid embedding ≈ mean of member vectors
        member_vecs = X[[i for i, lab in enumerate(labels) if int(lab) == cluster_idx]]
        centroid = member_vecs.mean(axis=0).tolist()
        cluster_id = f"c{cluster_idx}"
        doc_id = _stable_summary_id(document_id, generation, "cluster", cluster_id)
        cluster_docs.append(
            SearchDocument(
                id=doc_id,
                embedding=centroid,
                content=summary_text,
                project_id=project_id,
                document_id=document_id,
                rag_generation=generation,
                embedding_model=embedding.model_name,
                embedding_dimension=len(centroid),
                chunk_index=cluster_idx,
                summary_level="cluster",
                cluster_id=cluster_id,
                member_chunk_ids=member_ids,
                filename=filename,
                extra={"summary_kind": "cluster"},
            )
        )

    manifesto_id: str | None = None
    if cluster_summaries:
        joined = "\n\n".join(
            f"Cluster {i + 1}: {s}" for i, s in enumerate(cluster_summaries)
        )
        manifesto_prompt = render_prompt(
            "document_manifesto",
            filename=filename,
            cluster_summaries=joined,
        )
        manifesto_resp = await llm.complete(
            messages=[
                {
                    "role": "system",
                    "content": "You write a short document manifesto for RAG retrieval.",
                },
                {"role": "user", "content": manifesto_prompt},
            ],
            temperature=0.2,
            max_tokens=config.manifesto_max_tokens,
        )
        manifesto_text = (manifesto_resp.content or "").strip()
        if not manifesto_text:
            manifesto_text = "\n".join(cluster_summaries[:5])

        manifesto_emb = embedding.embed(manifesto_text)
        manifesto_id = _stable_summary_id(
            document_id, generation, "document", "manifesto"
        )
        all_member_ids = [h.id for h in chunk_hits]
        cluster_docs.append(
            SearchDocument(
                id=manifesto_id,
                embedding=manifesto_emb,
                content=manifesto_text,
                project_id=project_id,
                document_id=document_id,
                rag_generation=generation,
                embedding_model=embedding.model_name,
                embedding_dimension=len(manifesto_emb),
                chunk_index=0,
                summary_level="document",
                cluster_id="manifesto",
                member_chunk_ids=all_member_ids,
                filename=filename,
                extra={"summary_kind": "manifesto"},
            )
        )

    if cluster_docs:
        store.upsert(cluster_docs)

    logger.info(
        "Summary upsert complete document=%s clusters=%d manifesto=%s",
        document_id,
        len(cluster_docs) - (1 if manifesto_id else 0),
        manifesto_id,
    )
    return SummaryJobResult(
        document_id=document_id,
        project_id=project_id,
        cluster_count=len(cluster_docs) - (1 if manifesto_id else 0),
        manifesto_id=manifesto_id,
    )


def summary_meta_payload(result: SummaryJobResult) -> dict[str, Any]:
    return {
        "cluster_count": result.cluster_count,
        "manifesto_id": result.manifesto_id,
        "skipped": result.skipped,
        "reason": result.reason,
    }
