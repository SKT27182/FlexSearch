"""Suggested questions from manifesto / clusters / graph + follow-ups."""

from __future__ import annotations

import json
import re
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Document, DocumentStatus, Project, RagMode
from app.prompts import render_prompt
from app.services.llm import get_llm_service
from app.services.search_store import get_search_store
from app.services.search_store.types import SearchFilters
from app.utils.logger import create_logger

logger = create_logger(__name__)


def _parse_questions(raw: str, *, limit: int) -> list[str]:
    text = raw.strip()
    # Strip markdown fences if present
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            questions = [str(q).strip() for q in parsed if str(q).strip()]
            return questions[:limit]
        if isinstance(parsed, dict) and "questions" in parsed:
            questions = [str(q).strip() for q in parsed["questions"] if str(q).strip()]
            return questions[:limit]
    except json.JSONDecodeError:
        pass
    # Fallback: numbered / bulleted lines
    lines = []
    for line in text.splitlines():
        cleaned = re.sub(r"^[\d\-*\.\)]+\s*", "", line).strip()
        if cleaned.endswith("?"):
            lines.append(cleaned)
    return lines[:limit]


async def _gather_vector_context(project_id: UUID, *, max_chars: int = 8000) -> str:
    store = get_search_store()
    parts: list[str] = []

    # Document manifesto first
    manifesto, _ = store.scroll(
        filters=SearchFilters(
            project_id=str(project_id),
            summary_level="document",
        ),
        size=20,
    )
    for hit in manifesto:
        parts.append(f"[manifesto] {hit.content}")

    # Cluster summaries
    clusters, _ = store.scroll(
        filters=SearchFilters(
            project_id=str(project_id),
            summary_level="cluster",
        ),
        size=30,
    )
    for hit in clusters[:15]:
        parts.append(f"[cluster] {hit.content}")

    if not parts:
        # Fallback: sample chunk content
        chunks, _ = store.scroll(
            filters=SearchFilters(
                project_id=str(project_id),
                summary_level="chunk",
            ),
            size=10,
        )
        for hit in chunks:
            parts.append(hit.content[:500])

    combined = "\n\n---\n\n".join(parts)
    if len(combined) > max_chars:
        return combined[:max_chars] + "…"
    return combined


async def _gather_graph_context(project_id: UUID, *, limit: int = 30) -> str:
    try:
        from app.services.neo4j_store import get_neo4j_store

        store = get_neo4j_store()
        entities = store._search_entities_fulltext(str(project_id), "", limit)
        if not entities:
            return ""
        lines = []
        for ent in entities:
            name = ent.get("name") or ""
            desc = (ent.get("description") or "")[:200]
            lines.append(f"- {name}: {desc}" if desc else f"- {name}")
        return "Graph entities:\n" + "\n".join(lines)
    except Exception as exc:
        logger.warning("Graph context for suggestions failed: %s", exc)
        return ""


async def generate_project_suggestions(
    db: AsyncSession,
    project_id: UUID,
    *,
    count: int = 5,
) -> list[str]:
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise ValueError("Project not found")

    rag_mode = project.rag_mode
    if isinstance(rag_mode, str):
        rag_mode = RagMode(rag_mode)

    context = ""
    if rag_mode == RagMode.GRAPH:
        context = await _gather_graph_context(project_id)
        # Also try Microsoft GraphRAG workspace summaries if neo4j empty
        if not context:
            docs = await db.execute(
                select(Document)
                .where(Document.project_id == project_id)
                .where(Document.status == DocumentStatus.COMPLETED)
                .limit(5)
            )
            names = [d.filename for d in docs.scalars().all()]
            context = "Documents: " + ", ".join(names) if names else ""
    else:
        context = await _gather_vector_context(project_id)

    if not context.strip():
        return [
            "What are the main topics covered in this project?",
            "Can you summarize the key documents?",
            "What should I know first about this corpus?",
        ][:count]

    prompt = render_prompt(
        "suggested_questions",
        project_name=project.name,
        context=context,
        count=count,
    )
    llm = get_llm_service()
    response = await llm.complete(
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": "Generate the suggested questions as JSON."},
        ],
        temperature=0.6,
        max_tokens=512,
    )
    questions = _parse_questions(response.content, limit=count)
    return questions or [
        "What are the main themes in these documents?",
        "Summarize the most important findings.",
    ][:count]


async def generate_followup_questions(
    *,
    query: str,
    answer: str,
    project_id: UUID | None = None,
    count: int = 3,
) -> list[str]:
    context = ""
    if project_id is not None:
        try:
            context = await _gather_vector_context(project_id, max_chars=4000)
        except Exception:
            context = ""

    prompt = render_prompt(
        "followup",
        query=query,
        answer=answer[:3000],
        context=context or None,
        count=count,
    )
    llm = get_llm_service()
    response = await llm.complete(
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": "Generate follow-up questions as JSON."},
        ],
        temperature=0.6,
        max_tokens=400,
    )
    return _parse_questions(response.content, limit=count)
