"""Chat history persistence (Postgres)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import ChatSession, ChatTurn, User
from app.utils.logger import create_logger

logger = create_logger(__name__)


def _default_title(question: str) -> str:
    text = " ".join(question.strip().split())
    if len(text) <= 60:
        return text or "New chat"
    return text[:57] + "..."


class ChatHistoryService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create_session(
        self,
        *,
        project_id: UUID,
        user_id: UUID,
        title: str | None = None,
    ) -> ChatSession:
        session = ChatSession(
            project_id=project_id,
            user_id=user_id,
            title=title or "New chat",
        )
        self.db.add(session)
        await self.db.commit()
        await self.db.refresh(session)
        return session

    async def get_session(
        self,
        session_id: UUID,
        *,
        user_id: UUID | None = None,
        load_turns: bool = False,
    ) -> ChatSession | None:
        stmt = select(ChatSession).where(ChatSession.id == session_id)
        if user_id is not None:
            stmt = stmt.where(ChatSession.user_id == user_id)
        if load_turns:
            stmt = stmt.options(selectinload(ChatSession.turns))
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def list_sessions(
        self,
        *,
        project_id: UUID,
        user_id: UUID,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[ChatSession], int]:
        count_stmt = (
            select(func.count())
            .select_from(ChatSession)
            .where(
                ChatSession.project_id == project_id,
                ChatSession.user_id == user_id,
            )
        )
        total = int((await self.db.execute(count_stmt)).scalar_one())
        stmt = (
            select(ChatSession)
            .where(
                ChatSession.project_id == project_id,
                ChatSession.user_id == user_id,
            )
            .order_by(ChatSession.updated_at.desc())
            .offset(offset)
            .limit(limit)
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all()), total

    async def delete_session(
        self,
        session_id: UUID,
        *,
        user_id: UUID,
    ) -> bool:
        session = await self.get_session(session_id, user_id=user_id)
        if not session:
            return False
        await self.db.delete(session)
        await self.db.commit()
        return True

    async def list_turns(
        self,
        session_id: UUID,
        *,
        user_id: UUID,
    ) -> list[ChatTurn] | None:
        session = await self.get_session(session_id, user_id=user_id, load_turns=True)
        if not session:
            return None
        return list(session.turns)

    async def turns_as_memory(
        self,
        session_id: UUID,
        *,
        user_id: UUID,
        project_id: UUID,
        max_turns: int = 10,
    ) -> list[dict[str, Any]]:
        """Load recent turns only after owner and project authorization."""
        session = await self.get_session(session_id, user_id=user_id, load_turns=True)
        if not session or session.project_id != project_id:
            return []
        turns = list(session.turns)[-max_turns:]
        return [{"role": t.role, "content": t.content} for t in turns]

    async def authorize_session(
        self,
        session_id: UUID,
        *,
        user_id: UUID,
        project_id: UUID,
    ) -> ChatSession | None:
        """Return a session only when both caller and project own it."""
        session = await self.get_session(session_id, user_id=user_id)
        if session is None or session.project_id != project_id:
            return None
        return session

    async def ensure_session(
        self,
        *,
        project_id: UUID,
        user: User,
        session_id: UUID | None,
        question: str,
    ) -> ChatSession:
        if session_id is not None:
            existing = await self.get_session(session_id, user_id=user.id)
            if existing and existing.project_id == project_id:
                return existing
        return await self.create_session(
            project_id=project_id,
            user_id=user.id,
            title=_default_title(question),
        )

    async def add_exchange(
        self,
        session: ChatSession,
        *,
        question: str,
        answer: str,
        citations: list[dict[str, Any]] | None = None,
        retrieval_strategy: str | None = None,
        reranking_strategy: str | None = None,
        model: str | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        latency_ms: int | None = None,
    ) -> tuple[ChatTurn, ChatTurn]:
        user_turn = ChatTurn(
            session_id=session.id,
            role="user",
            content=question,
        )
        assistant_turn = ChatTurn(
            session_id=session.id,
            role="assistant",
            content=answer,
            citations=citations,
            retrieval_strategy=retrieval_strategy,
            reranking_strategy=reranking_strategy,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
        )
        self.db.add(user_turn)
        self.db.add(assistant_turn)
        session.updated_at = datetime.now(timezone.utc)
        if session.title in (None, "", "New chat"):
            session.title = _default_title(question)
        await self.db.commit()
        await self.db.refresh(user_turn)
        await self.db.refresh(assistant_turn)
        await self.db.refresh(session)
        return user_turn, assistant_turn
