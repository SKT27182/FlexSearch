"""Tests for project deletion index cleanup."""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import select

from app.db.models import OutboxEvent, Project, RagMode
from app.services.project_lifecycle import delete_project_fully


@pytest.mark.asyncio
async def test_delete_project_fully_creates_durable_cleanup_tombstone(
    db_session,
) -> None:
    project_id = uuid4()

    project = Project(
        id=project_id,
        name="GraphRAG Delete",
        owner_id=uuid4(),
        rag_mode=RagMode.GRAPH,
        rag_config={"graph_backend": "microsoft"},
    )
    db_session.add(project)
    await db_session.commit()

    await delete_project_fully(db_session, project)
    await db_session.commit()

    await db_session.refresh(project)
    assert project.deleting_at is not None
    event = (
        await db_session.execute(
            select(OutboxEvent).where(
                OutboxEvent.aggregate_id == project_id,
                OutboxEvent.event_type == "cleanup_project",
            )
        )
    ).scalar_one()
    assert event.project_id == project_id
