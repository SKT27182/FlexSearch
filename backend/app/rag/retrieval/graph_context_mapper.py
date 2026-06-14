"""Map GraphRAG search context payloads to RetrievalResult objects."""

from __future__ import annotations

from typing import Any

import pandas as pd

from app.rag.retrieval.base import RetrievalResult

_TEXT_COLUMNS = (
    "text",
    "content",
    "description",
    "full_content",
    "summary",
    "title",
    "entity",
    "relationship",
)
_ID_COLUMNS = ("id", "chunk_id", "text_unit_id", "entity_id", "community_id")


def _row_text(row: dict[str, Any]) -> str:
    for key in _TEXT_COLUMNS:
        val = row.get(key)
        if val is not None and str(val).strip():
            return str(val).strip()
    return str(row)


def _row_id(row: dict[str, Any], fallback: str) -> str:
    for key in _ID_COLUMNS:
        val = row.get(key)
        if val is not None and str(val).strip():
            return str(val)
    return fallback


def _row_document_id(row: dict[str, Any]) -> str:
    for key in ("document_id", "source_document_id", "doc_id"):
        val = row.get(key)
        if val is not None and str(val).strip():
            return str(val)
    return ""


def _records_from_dataframe(df: pd.DataFrame, source: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for idx, row in df.iterrows():
        data = row.to_dict()
        data.setdefault("source", source)
        records.append(data)
    if not records and not df.empty:
        records.append({"source": source, "text": df.to_string()})
    return records


def context_to_retrieval_results(
    context: Any,
    *,
    top_k: int,
) -> list[RetrievalResult]:
    """Convert GraphRAG context (dict of lists / DataFrames) to ranked chunks."""
    records: list[dict[str, Any]] = []

    if context is None:
        return []

    if isinstance(context, pd.DataFrame):
        records.extend(_records_from_dataframe(context, "graph_context"))
    elif isinstance(context, list):
        for item in context:
            if isinstance(item, pd.DataFrame):
                records.extend(_records_from_dataframe(item, "graph_context"))
            elif isinstance(item, dict):
                item.setdefault("source", "graph_context")
                records.append(item)
            elif isinstance(item, str) and item.strip():
                records.append({"source": "graph_context", "text": item})
    elif isinstance(context, dict):
        for source, value in context.items():
            if isinstance(value, pd.DataFrame):
                records.extend(_records_from_dataframe(value, str(source)))
            elif isinstance(value, list):
                for entry in value:
                    if isinstance(entry, dict):
                        entry.setdefault("source", str(source))
                        records.append(entry)
                    elif isinstance(entry, str) and entry.strip():
                        records.append({"source": str(source), "text": entry})
            elif isinstance(value, str) and value.strip():
                records.append({"source": str(source), "text": value})
    elif isinstance(context, str) and context.strip():
        records.append({"source": "graph_context", "text": context})

    results: list[RetrievalResult] = []
    for idx, row in enumerate(records[:top_k]):
        content = _row_text(row)
        if not content:
            continue
        chunk_id = _row_id(row, f"graph-{idx}")
        document_id = _row_document_id(row)
        metadata = {
            k: v
            for k, v in row.items()
            if k not in {"text", "content", "description", "full_content"}
        }
        metadata.setdefault("source", row.get("source", "graph_context"))
        score = float(row.get("score", row.get("rank", 1.0 - idx * 0.01)))
        results.append(
            RetrievalResult(
                content=content,
                score=score,
                document_id=document_id,
                chunk_id=chunk_id,
                metadata=metadata,
            )
        )
    return results
