"""
FlexSearch Backend - Chat API

E2E RAG chat: retrieve → LLM answer → citations → SSE stream + history.
"""

from __future__ import annotations

from typing import Annotated, Any, AsyncGenerator
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.dependencies import get_current_active_user, get_db
from app.core.rate_limit import CHAT_RULE, check_rate_limit
from app.db.models import Project, RagMode, User
from app.rag.chat import ChatOrchestrator, format_sse
from app.schemas.chat import (
    ChatCitation,
    ChatQueryRequest,
    ChatQueryResponse,
    ChatSessionCreate,
    ChatSessionListResponse,
    ChatSessionResponse,
    ChatTurnListResponse,
    ChatTurnResponse,
)
from app.schemas.graph_index import GraphIndexState
from app.schemas.rag_config import parse_rag_config
from app.services.chat_history import ChatHistoryService
from app.services.neo4j_store import Neo4jStoreError
from app.services.project_access import user_can_access_project
from app.services.retrieval_validation import validate_retrieval_for_mode
from app.services.session_memory import SessionMemoryService
from app.utils.logger import create_logger

logger = create_logger(__name__, level=settings.log_level)

router = APIRouter(prefix="/chat", tags=["chat"])

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


async def _load_accessible_project(
    db: AsyncSession,
    project_id: str,
    user: User,
) -> Project:
    try:
        project_uuid = UUID(project_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid project ID format",
        ) from exc

    result = await db.execute(
        select(Project).where(Project.id == project_uuid, Project.deleting_at.is_(None))
    )
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if not user_can_access_project(user, project):
        raise HTTPException(status_code=403, detail="Not authorized")
    return project


def _ensure_graph_ready(project: Project) -> None:
    if project.rag_transition_status == "switching":
        raise HTTPException(status_code=409, detail="RAG generation is switching")
    rag_mode = project.rag_mode
    if isinstance(rag_mode, str):
        rag_mode = RagMode(rag_mode)
    if rag_mode != RagMode.GRAPH:
        return
    graph_state = GraphIndexState.from_db(project.graph_index_status)
    if graph_state.status != "ready":
        raise HTTPException(
            status_code=409,
            detail="Graph index is not ready. Wait for indexing to complete.",
        )


def _session_response(session, turn_count: int | None = None) -> ChatSessionResponse:
    return ChatSessionResponse(
        id=str(session.id),
        project_id=str(session.project_id),
        user_id=str(session.user_id),
        title=session.title,
        created_at=session.created_at,
        updated_at=session.updated_at,
        turn_count=turn_count,
    )


def _turn_response(turn) -> ChatTurnResponse:
    return ChatTurnResponse(
        id=str(turn.id),
        session_id=str(turn.session_id),
        role=turn.role,
        content=turn.content,
        citations=turn.citations,
        retrieval_strategy=turn.retrieval_strategy,
        reranking_strategy=turn.reranking_strategy,
        model=turn.model,
        input_tokens=turn.input_tokens,
        output_tokens=turn.output_tokens,
        latency_ms=turn.latency_ms,
        created_at=turn.created_at,
    )


@router.post("/query", response_model=ChatQueryResponse)
async def chat_query(
    request: ChatQueryRequest,
    http_request: Request,
    current_user: Annotated[User, Depends(get_current_active_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ChatQueryResponse:
    """Non-streaming RAG chat: retrieve + LLM answer + citations."""
    await check_rate_limit(http_request, CHAT_RULE, user_id=str(current_user.id))
    project = await _load_accessible_project(db, request.project_id, current_user)
    rag_mode = project.rag_mode
    if isinstance(rag_mode, str):
        rag_mode = RagMode(rag_mode)
    rag_config = parse_rag_config(rag_mode, project.rag_config)
    validation_error = validate_retrieval_for_mode(
        rag_mode, rag_config, request.overrides
    )
    if validation_error:
        raise HTTPException(status_code=400, detail=validation_error)
    _ensure_graph_ready(project)

    history = ChatHistoryService(db)
    session_uuid: UUID | None = None
    if request.session_id:
        try:
            session_uuid = UUID(request.session_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid session ID") from exc
        if (
            await history.authorize_session(
                session_uuid,
                user_id=current_user.id,
                project_id=project.id,
            )
            is None
        ):
            raise HTTPException(status_code=404, detail="Chat session not found")

    session = None
    if request.persist:
        session = await history.ensure_session(
            project_id=project.id,
            user=current_user,
            session_id=session_uuid,
            question=request.query,
        )
        session_uuid = session.id

    orchestrator = ChatOrchestrator(db, project)
    try:
        result = await orchestrator.answer(
            request.query,
            session_id=session_uuid,
            session_user_id=current_user.id if session_uuid else None,
            session_project_id=project.id if session_uuid else None,
            top_k=request.top_k,
            overrides=request.overrides,
        )
    except Neo4jStoreError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except TimeoutError as exc:
        raise HTTPException(status_code=504, detail=str(exc)) from exc

    turn_id = None
    if request.persist and session is not None:
        _, assistant_turn = await history.add_exchange(
            session,
            question=request.query,
            answer=result.answer,
            citations=[c.to_dict() for c in result.citations],
            retrieval_strategy=result.retrieval_strategy,
            reranking_strategy=result.reranking_strategy,
            model=result.model,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            latency_ms=result.latency_ms,
        )
        turn_id = str(assistant_turn.id)
        await orchestrator.persist_turn_memory(
            session.id,
            question=request.query,
            answer=result.answer,
        )

    return ChatQueryResponse(
        project_id=request.project_id,
        query=request.query,
        answer=result.answer,
        citations=[ChatCitation(**c.to_dict()) for c in result.citations],
        retrieval_strategy=result.retrieval_strategy,
        reranking_strategy=result.reranking_strategy,
        session_id=str(session_uuid) if session_uuid else None,
        turn_id=turn_id,
        model=result.model,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        latency_ms=result.latency_ms,
        empty_retrieval=result.empty_retrieval,
        grounded=result.grounded,
        invalid_citations=result.invalid_citations,
        debug=result.debug,
    )


@router.post("/stream")
async def chat_stream(
    request: ChatQueryRequest,
    http_request: Request,
    current_user: Annotated[User, Depends(get_current_active_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> StreamingResponse:
    """SSE streaming RAG chat."""
    await check_rate_limit(http_request, CHAT_RULE, user_id=str(current_user.id))
    project = await _load_accessible_project(db, request.project_id, current_user)
    rag_mode = project.rag_mode
    if isinstance(rag_mode, str):
        rag_mode = RagMode(rag_mode)
    rag_config = parse_rag_config(rag_mode, project.rag_config)
    validation_error = validate_retrieval_for_mode(
        rag_mode, rag_config, request.overrides
    )
    if validation_error:
        raise HTTPException(status_code=400, detail=validation_error)
    _ensure_graph_ready(project)

    history = ChatHistoryService(db)
    session_uuid: UUID | None = None
    if request.session_id:
        try:
            session_uuid = UUID(request.session_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid session ID") from exc
        if (
            await history.authorize_session(
                session_uuid,
                user_id=current_user.id,
                project_id=project.id,
            )
            is None
        ):
            raise HTTPException(status_code=404, detail="Chat session not found")

    session = None
    if request.persist:
        session = await history.ensure_session(
            project_id=project.id,
            user=current_user,
            session_id=session_uuid,
            question=request.query,
        )
        session_uuid = session.id

    orchestrator = ChatOrchestrator(db, project)

    async def event_gen() -> AsyncGenerator[str, None]:
        final_answer = ""
        final_citations: list[dict[str, Any]] = []
        meta: dict[str, Any] = {}
        try:
            if session_uuid:
                yield format_sse("session", {"session_id": str(session_uuid)})
            async for event, payload in orchestrator.stream(
                request.query,
                session_id=session_uuid,
                session_user_id=current_user.id if session_uuid else None,
                session_project_id=project.id if session_uuid else None,
                top_k=request.top_k,
                overrides=request.overrides,
            ):
                if await http_request.is_disconnected():
                    return
                if event == "citations":
                    final_citations = list(payload.get("citations") or [])
                if event == "done":
                    final_answer = payload.get("answer") or ""
                    meta = payload
                    if not final_citations:
                        final_citations = list(payload.get("citations") or [])
                yield format_sse(event, payload)

            if request.persist and session is not None and final_answer:
                _, assistant_turn = await history.add_exchange(
                    session,
                    question=request.query,
                    answer=final_answer,
                    citations=final_citations,
                    retrieval_strategy=meta.get("retrieval_strategy"),
                    reranking_strategy=meta.get("reranking_strategy"),
                    model=meta.get("model"),
                    input_tokens=meta.get("input_tokens"),
                    output_tokens=meta.get("output_tokens"),
                    latency_ms=meta.get("latency_ms"),
                )
                await orchestrator.persist_turn_memory(
                    session.id,
                    question=request.query,
                    answer=final_answer,
                )
                yield format_sse(
                    "persisted",
                    {
                        "session_id": str(session.id),
                        "turn_id": str(assistant_turn.id),
                    },
                )
            yield format_sse("close", {"reason": "complete"})
        except Neo4jStoreError:
            logger.exception("Chat graph backend failed")
            yield format_sse("error", {"detail": "Graph service unavailable"})
        except TimeoutError:
            logger.exception("Chat stream timed out")
            yield format_sse("error", {"detail": "Chat request timed out"})
        except Exception:
            logger.exception("Chat stream failed")
            yield format_sse("error", {"detail": "Chat stream failed"})

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


@router.post("/sessions", response_model=ChatSessionResponse, status_code=201)
async def create_chat_session(
    body: ChatSessionCreate,
    current_user: Annotated[User, Depends(get_current_active_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ChatSessionResponse:
    project = await _load_accessible_project(db, body.project_id, current_user)
    session = await ChatHistoryService(db).create_session(
        project_id=project.id,
        user_id=current_user.id,
        title=body.title,
    )
    return _session_response(session, turn_count=0)


@router.get("/sessions", response_model=ChatSessionListResponse)
async def list_chat_sessions(
    current_user: Annotated[User, Depends(get_current_active_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    project_id: str = Query(...),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> ChatSessionListResponse:
    project = await _load_accessible_project(db, project_id, current_user)
    sessions, total = await ChatHistoryService(db).list_sessions(
        project_id=project.id,
        user_id=current_user.id,
        limit=limit,
        offset=offset,
    )
    return ChatSessionListResponse(
        sessions=[_session_response(s) for s in sessions],
        total=total,
    )


@router.get("/sessions/{session_id}", response_model=ChatSessionResponse)
async def get_chat_session(
    session_id: str,
    current_user: Annotated[User, Depends(get_current_active_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ChatSessionResponse:
    try:
        sid = UUID(session_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid session ID") from exc
    history = ChatHistoryService(db)
    session = await history.get_session(sid, user_id=current_user.id, load_turns=True)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    project = await _load_accessible_project(db, str(session.project_id), current_user)
    _ = project
    return _session_response(session, turn_count=len(session.turns))


@router.delete("/sessions/{session_id}", status_code=204)
async def delete_chat_session(
    session_id: str,
    current_user: Annotated[User, Depends(get_current_active_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    try:
        sid = UUID(session_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid session ID") from exc
    history = ChatHistoryService(db)
    session = await history.get_session(sid, user_id=current_user.id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    await _load_accessible_project(db, str(session.project_id), current_user)
    await history.delete_session(sid, user_id=current_user.id)
    await SessionMemoryService().clear(sid)


@router.get("/sessions/{session_id}/turns", response_model=ChatTurnListResponse)
async def list_chat_turns(
    session_id: str,
    current_user: Annotated[User, Depends(get_current_active_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ChatTurnListResponse:
    try:
        sid = UUID(session_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid session ID") from exc
    history = ChatHistoryService(db)
    session = await history.get_session(sid, user_id=current_user.id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    await _load_accessible_project(db, str(session.project_id), current_user)
    turns = await history.list_turns(sid, user_id=current_user.id)
    return ChatTurnListResponse(
        session_id=session_id,
        turns=[_turn_response(t) for t in (turns or [])],
    )
