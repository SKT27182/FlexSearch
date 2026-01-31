"""
FlexSearch Backend - Chat WebSocket Router

Real-time chat with RAG pipeline integration and context management.
"""

import json
import time
from datetime import datetime, timezone
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.dependencies import get_db
from app.core.security import decode_access_token
from app.db.models import Project, User
from app.db.redis import RedisSessionStore, get_redis
from app.rag.pipeline import get_rag_pipeline
from app.schemas.session import ChatMessage
from app.services.llm import get_llm_service
from app.services.token_tracker import track_usage
from app.utils.logger import create_logger

logger = create_logger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])

# Redis key patterns
SESSION_KEY = "session:{session_id}"
SESSION_MESSAGES_KEY = "session:{session_id}:messages"

# Default context window (-1 = all messages)
DEFAULT_CONTEXT_MESSAGES = -1
MAX_CONTEXT_TOKENS = 4096  # Future: summarize if exceeding


async def get_session_store() -> RedisSessionStore:
    """Get Redis session store."""
    redis = await get_redis()
    return RedisSessionStore(redis)


async def authenticate_websocket(
    websocket: WebSocket,
    db: AsyncSession,
) -> User | None:
    """Authenticate WebSocket connection via token query param."""
    token = websocket.query_params.get("token")

    if not token:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return None

    payload = decode_access_token(token)
    if not payload:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return None

    user_id = payload.get("sub")
    if not user_id:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return None

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if not user:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return None

    return user


async def get_conversation_context(
    store: RedisSessionStore,
    session_id: str,
    n_messages: int = -1,
) -> list[dict[str, str]]:
    """
    Get previous conversation context from Redis.

    Args:
        store: Redis session store
        session_id: Session ID
        n_messages: Number of previous messages to include (-1 for all)

    Returns:
        List of messages in LLM format [{"role": ..., "content": ...}]
    """
    key = SESSION_MESSAGES_KEY.format(session_id=session_id)

    if n_messages == -1:
        # Get all messages
        messages_json = await store.lrange(key, 0, -1)
    else:
        # Get last n messages
        messages_json = await store.lrange(key, -n_messages, -1)

    messages = []
    for msg_json in messages_json:
        try:
            msg = json.loads(msg_json)
            messages.append(
                {
                    "role": msg.get("role", "user"),
                    "content": msg.get("content", ""),
                }
            )
        except json.JSONDecodeError:
            continue

    return messages


def estimate_token_count(messages: list[dict[str, str]]) -> int:
    """Rough token estimation (4 chars ≈ 1 token)."""
    total_chars = sum(len(m.get("content", "")) for m in messages)
    return total_chars // 4


async def summarize_context_if_needed(
    messages: list[dict[str, str]],
    max_tokens: int = MAX_CONTEXT_TOKENS,
) -> list[dict[str, str]]:
    """
    Summarize conversation context if it exceeds token limit.

    Future implementation: Use LLM to summarize older messages.
    For now, truncates to most recent messages.
    """
    token_count = estimate_token_count(messages)

    if token_count <= max_tokens:
        return messages

    # For now, truncate to recent messages
    # Future: Use LLM to summarize older context
    logger.warning(f"Context exceeds {max_tokens} tokens, truncating")

    # Keep removing oldest messages until under limit
    while messages and estimate_token_count(messages) > max_tokens:
        messages = messages[1:]

    return messages


@router.websocket("/ws/{session_id}")
async def chat_websocket(
    websocket: WebSocket,
    session_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    context_messages: int = Query(
        default=-1, description="Number of previous messages to include (-1 for all)"
    ),
) -> None:
    """
    WebSocket endpoint for real-time chat.

    Message format:
    - Client sends: {"message": "user query"}
    - Server sends: {"type": "start|chunk|end|error", "content": "..."}

    Query params:
    - token: JWT access token
    - context_messages: Number of previous messages to include (-1 for all)
    """
    await websocket.accept()

    # Authenticate
    user = await authenticate_websocket(websocket, db)
    if not user:
        return

    store = await get_session_store()

    # Verify session exists and belongs to user
    session_data = await store.get(SESSION_KEY.format(session_id=session_id))
    if not session_data:
        await websocket.send_json({"type": "error", "content": "Session not found"})
        await websocket.close()
        return

    session_info = json.loads(session_data)
    if session_info["user_id"] != str(user.id):
        await websocket.send_json({"type": "error", "content": "Not authorized"})
        await websocket.close()
        return

    # Convert project_id from string to UUID for database query
    try:
        project_id = UUID(session_info["project_id"])
    except (ValueError, KeyError) as e:
        logger.error(f"Invalid project_id in session {session_id}: {e}")
        await websocket.send_json({"type": "error", "content": "Invalid session data"})
        await websocket.close()
        return

    # Verify project exists
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        await websocket.send_json({"type": "error", "content": "Project not found"})
        await websocket.close()
        return

    logger.info(f"WebSocket connected: session={session_id}, user={user.email}")

    # Get services
    rag_pipeline = get_rag_pipeline()
    llm_service = get_llm_service()

    try:
        while True:
            # Receive message
            data = await websocket.receive_json()
            user_message = data.get("message", "").strip()
            n_context = data.get("context_messages", context_messages)

            if not user_message:
                continue

            # Store user message in Redis
            now = datetime.now(timezone.utc)
            user_msg = ChatMessage(role="user", content=user_message)
            await store.rpush(
                SESSION_MESSAGES_KEY.format(session_id=session_id),
                user_msg.model_dump_json(),
            )

            # Signal start of response
            await websocket.send_json({"type": "start"})

            try:
                # Start latency tracking
                start_time = time.perf_counter()

                # Get conversation context
                context_messages_list = await get_conversation_context(
                    store, session_id, n_context
                )

                # Summarize if too long
                context_messages_list = await summarize_context_if_needed(
                    context_messages_list
                )

                # Retrieve relevant chunks from RAG
                rag_result = await rag_pipeline.query(
                    query=user_message,
                    project_id=str(project_id),
                    top_k=5,
                )

                # Build messages for LLM
                system_prompt = f"""You are a helpful assistant that answers questions based on the provided context.
Answer based on the retrieved context when relevant. If the context doesn't contain relevant information,
use your general knowledge but mention this. Always be accurate and helpful.

Retrieved Context:
{rag_result['context']}"""

                messages = [
                    {"role": "system", "content": system_prompt},
                    *context_messages_list,  # Include conversation history
                    {"role": "user", "content": user_message},
                ]

                # Stream LLM response
                full_response = ""
                input_tokens = 0
                output_tokens = 0

                async for chunk in llm_service.stream(messages):
                    if chunk.content:
                        full_response += chunk.content
                        await websocket.send_json(
                            {
                                "type": "chunk",
                                "content": chunk.content,
                            }
                        )

                    if chunk.is_final:
                        input_tokens = chunk.input_tokens or 0
                        output_tokens = chunk.output_tokens or 0

                # Calculate latency in milliseconds
                end_time = time.perf_counter()
                latency_ms = int((end_time - start_time) * 1000)

                # Store assistant message in Redis
                assistant_msg = ChatMessage(role="assistant", content=full_response)
                await store.rpush(
                    SESSION_MESSAGES_KEY.format(session_id=session_id),
                    assistant_msg.model_dump_json(),
                )

                # Track token usage
                await track_usage(
                    db=db,
                    user_id=user.id,
                    project_id=project.id,
                    session_id=session_id,
                    model_name=llm_service.model_name,
                    provider=llm_service.provider,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    latency_ms=latency_ms,
                )

                # Update session timestamp
                session_info["updated_at"] = now.isoformat()
                await store.set(
                    SESSION_KEY.format(session_id=session_id),
                    json.dumps(session_info),
                    expire_seconds=86400 * 7,
                )

                # Signal end of response with sources
                await websocket.send_json(
                    {
                        "type": "end",
                        "sources": rag_result["sources"],
                        "token_usage": {
                            "input": input_tokens,
                            "output": output_tokens,
                        },
                    }
                )

            except Exception as e:
                logger.error(f"RAG/LLM error: {e}")
                await websocket.send_json(
                    {
                        "type": "error",
                        "content": f"Processing error: {str(e)}",
                    }
                )

    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected: session={session_id}")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        await websocket.send_json({"type": "error", "content": str(e)})
        await websocket.close()


@router.get("/{session_id}/history")
async def get_chat_history(
    session_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: int = Query(
        default=-1, description="Number of messages to return (-1 for all)"
    ),
) -> list[dict]:
    """
    Get chat history for a session.

    Messages are stored only in Redis (ephemeral).
    """
    store = await get_session_store()

    # Get messages
    if limit == -1:
        messages = await store.lrange(
            SESSION_MESSAGES_KEY.format(session_id=session_id),
            0,
            -1,
        )
    else:
        messages = await store.lrange(
            SESSION_MESSAGES_KEY.format(session_id=session_id),
            -limit,
            -1,
        )

    return [json.loads(msg) for msg in messages]


@router.delete("/{session_id}/history", status_code=status.HTTP_204_NO_CONTENT)
async def clear_chat_history(
    session_id: str,
) -> None:
    """Clear chat history for a session (keeps session metadata)."""
    store = await get_session_store()
    await store.delete(SESSION_MESSAGES_KEY.format(session_id=session_id))
    logger.info(f"Cleared history for session: {session_id}")
