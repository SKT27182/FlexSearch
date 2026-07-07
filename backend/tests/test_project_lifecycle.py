"""Tests for project deletion index cleanup."""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.db.models import Project, RagMode
from app.services.project_lifecycle import delete_project_fully


@pytest.mark.asyncio
async def test_delete_project_fully_wipes_microsoft_graph_workspace(
    db_session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id = uuid4()
    wiped: list[tuple[str, str | None]] = []

    def _record_wipe(
        pid,
        *,
        from_mode: str,
        graph_backend: str | None = None,
    ) -> None:
        wiped.append((from_mode, graph_backend))

    monkeypatch.setattr(
        "app.services.project_lifecycle.wipe_index_for_mode",
        _record_wipe,
    )

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

    assert wiped == [("graph", "microsoft")]
