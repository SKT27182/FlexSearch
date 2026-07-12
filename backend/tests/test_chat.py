"""
FlexSearch Backend - Chat API Tests (vector + graph)
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.rag.retrieval.base import RetrievalResult
from app.services.llm import LLMResponse


async def create_user_and_login(client: AsyncClient, email: str) -> str:
    await client.post(
        "/api/auth/register",
        json={"email": email, "name": "Test User", "password": "password123"},
    )
    response = await client.post(
        "/api/auth/login",
        data={"username": email, "password": "password123"},
    )
    return response.json()["access_token"]


@pytest.fixture(autouse=True)
def _disable_redis_memory(monkeypatch):
    """Avoid Redis event-loop reuse across pytest async tests."""
    async def _no_redis():
        return None

    monkeypatch.setattr("app.services.session_memory.get_redis", _no_redis)
    monkeypatch.setattr("app.services.redis_client.get_redis", _no_redis)


class FakeLLM:
    model_name = "fake-model"

    async def complete(self, messages, temperature=0.7, max_tokens=1024, timeout_sec=120.0):
        return LLMResponse(
            content="Answer with citation [1].",
            input_tokens=10,
            output_tokens=5,
            model="fake-model",
            provider="test",
            latency_ms=1,
        )

    async def stream(self, messages, temperature=0.7, max_tokens=1024, timeout_sec=120.0):
        yield {"type": "token", "content": "Answer "}
        yield {"type": "token", "content": "with citation [1]."}
        yield {
            "type": "usage",
            "input_tokens": 10,
            "output_tokens": 5,
            "model": "fake-model",
            "latency_ms": 1,
        }


class FakePipeline:
    async def retrieve(self, query, project_id, top_k=5, overrides=None):
        results = [
            RetrievalResult(
                content="Relevant passage about FlexSearch.",
                score=0.91,
                document_id="doc-1",
                chunk_id="chunk-1",
                metadata={"filename": "guide.pdf", "chunk_index": 0},
            )
        ]
        return results[:top_k], "dense", "none"


class FakeGraphPipeline:
    async def retrieve(self, query, project_id, top_k=5, overrides=None):
        results = [
            RetrievalResult(
                content="Entity A relates to Entity B in the graph.",
                score=0.88,
                document_id="doc-g",
                chunk_id="passage-1",
                metadata={"filename": "graph.txt", "entity": "A"},
            )
        ]
        return results[:top_k], "graph_local", "none"


class TestChatQuery:
    async def test_query_requires_auth(self, async_client: AsyncClient) -> None:
        response = await async_client.post(
            "/api/chat/query",
            json={"project_id": "6c8ac9f8-df5c-4f4d-bc8c-cfe6608f9cf8", "query": "hi"},
        )
        assert response.status_code == 401

    async def test_vector_chat_returns_answer_and_citations(
        self, async_client: AsyncClient, db_session: AsyncSession, monkeypatch
    ) -> None:
        token = await create_user_and_login(async_client, "chat-vector@example.com")
        project_response = await async_client.post(
            "/api/projects",
            json={"name": "Chat Vector"},
            headers={"Authorization": f"Bearer {token}"},
        )
        project_id = project_response.json()["id"]

        monkeypatch.setattr(
            "app.rag.chat.orchestrator.create_pipeline",
            lambda config=None, rag_mode=None: FakePipeline(),
        )
        monkeypatch.setattr(
            "app.rag.chat.orchestrator.get_llm_service",
            lambda: FakeLLM(),
        )

        response = await async_client.post(
            "/api/chat/query",
            json={"project_id": project_id, "query": "What is FlexSearch?"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        payload = response.json()
        assert "Answer" in payload["answer"]
        assert payload["retrieval_strategy"] == "dense"
        assert len(payload["citations"]) == 1
        assert payload["citations"][0]["chunk_id"] == "chunk-1"
        assert payload["session_id"]
        assert payload["turn_id"]
        assert payload["empty_retrieval"] is False

        sessions = await async_client.get(
            "/api/chat/sessions",
            params={"project_id": project_id},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert sessions.status_code == 200
        assert sessions.json()["total"] >= 1
        session_id = sessions.json()["sessions"][0]["id"]

        turns = await async_client.get(
            f"/api/chat/sessions/{session_id}/turns",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert turns.status_code == 200
        assert len(turns.json()["turns"]) >= 2

    async def test_graph_chat_returns_answer(
        self, async_client: AsyncClient, db_session: AsyncSession, monkeypatch
    ) -> None:
        token = await create_user_and_login(async_client, "chat-graph@example.com")
        project_response = await async_client.post(
            "/api/projects",
            json={
                "name": "Chat Graph",
                "rag_mode": "graph",
                "rag_config": {
                    "graph_backend": "neo4j",
                    "extraction": {"strategy": "ocr", "passage_chunk_size": 800},
                    "indexing": {"max_entities_per_passage": 20, "embed_entities": True},
                    "retrieval": {"strategy": "graph_local", "params": {}},
                },
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert project_response.status_code == 201
        project_id = project_response.json()["id"]

        class FakeStats:
            passage_count = 3
            entity_count = 5

        class FakeNeo4j:
            def get_stats(self, project_id):
                return FakeStats()

        monkeypatch.setattr(
            "app.api.chat.get_neo4j_store",
            lambda: FakeNeo4j(),
        )
        monkeypatch.setattr(
            "app.rag.chat.orchestrator.create_pipeline",
            lambda config=None, rag_mode=None: FakeGraphPipeline(),
        )
        monkeypatch.setattr(
            "app.rag.chat.orchestrator.get_llm_service",
            lambda: FakeLLM(),
        )

        response = await async_client.post(
            "/api/chat/query",
            json={"project_id": project_id, "query": "How are entities related?"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["retrieval_strategy"] == "graph_local"
        assert len(payload["citations"]) == 1
        assert "Answer" in payload["answer"]

    async def test_chat_forbidden_for_other_user(
        self, async_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        owner = await create_user_and_login(async_client, "chat-owner@example.com")
        attacker = await create_user_and_login(async_client, "chat-attacker@example.com")
        project_response = await async_client.post(
            "/api/projects",
            json={"name": "Private"},
            headers={"Authorization": f"Bearer {owner}"},
        )
        project_id = project_response.json()["id"]
        response = await async_client.post(
            "/api/chat/query",
            json={"project_id": project_id, "query": "secret?"},
            headers={"Authorization": f"Bearer {attacker}"},
        )
        assert response.status_code == 403

    async def test_chat_stream_sse_events(
        self, async_client: AsyncClient, db_session: AsyncSession, monkeypatch
    ) -> None:
        token = await create_user_and_login(async_client, "chat-stream@example.com")
        project_response = await async_client.post(
            "/api/projects",
            json={"name": "Stream Project"},
            headers={"Authorization": f"Bearer {token}"},
        )
        project_id = project_response.json()["id"]

        monkeypatch.setattr(
            "app.rag.chat.orchestrator.create_pipeline",
            lambda config=None, rag_mode=None: FakePipeline(),
        )
        monkeypatch.setattr(
            "app.rag.chat.orchestrator.get_llm_service",
            lambda: FakeLLM(),
        )

        response = await async_client.post(
            "/api/chat/stream",
            json={"project_id": project_id, "query": "stream please"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        assert "text/event-stream" in response.headers["content-type"]
        body = response.text
        assert "event: session" in body or "event: status" in body
        assert "event: citations" in body
        assert "event: token" in body
        assert "event: done" in body
        assert "event: close" in body

    async def test_empty_retrieval_message(
        self, async_client: AsyncClient, db_session: AsyncSession, monkeypatch
    ) -> None:
        token = await create_user_and_login(async_client, "chat-empty@example.com")
        project_response = await async_client.post(
            "/api/projects",
            json={"name": "Empty"},
            headers={"Authorization": f"Bearer {token}"},
        )
        project_id = project_response.json()["id"]

        class EmptyPipeline:
            async def retrieve(self, query, project_id, top_k=5, overrides=None):
                return [], "dense", "none"

        monkeypatch.setattr(
            "app.rag.chat.orchestrator.create_pipeline",
            lambda config=None, rag_mode=None: EmptyPipeline(),
        )
        monkeypatch.setattr(
            "app.rag.chat.orchestrator.get_llm_service",
            lambda: FakeLLM(),
        )

        response = await async_client.post(
            "/api/chat/query",
            json={"project_id": project_id, "query": "nothing here"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["empty_retrieval"] is True
        assert payload["citations"] == []
        assert "could not find" in payload["answer"].lower()

    async def test_multi_query_and_debug_sse(
        self, async_client: AsyncClient, db_session: AsyncSession, monkeypatch
    ) -> None:
        token = await create_user_and_login(async_client, "chat-mq@example.com")
        project_response = await async_client.post(
            "/api/projects",
            json={
                "name": "MultiQuery",
                "rag_config": {
                    "extraction": {"strategy": "ocr"},
                    "chunking": {"strategy": "fixed_window", "params": {}},
                    "retrieval": {"strategy": "dense", "params": {}},
                    "reranking": {"strategy": "none", "params": {}},
                    "chat": {
                        "multi_query": {"enabled": True, "count": 2},
                        "debug": True,
                    },
                },
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        project_id = project_response.json()["id"]

        class CountingPipeline:
            calls = 0

            async def retrieve(self, query, project_id, top_k=5, overrides=None):
                CountingPipeline.calls += 1
                return (
                    [
                        RetrievalResult(
                            content=f"hit for {query}",
                            score=0.8,
                            document_id="d1",
                            chunk_id=f"c-{CountingPipeline.calls}",
                            metadata={"chunk_index": 0, "filename": "a.txt"},
                        )
                    ],
                    "dense",
                    "none",
                )

        class StageLLM(FakeLLM):
            async def complete(
                self, messages, temperature=0.7, max_tokens=1024, timeout_sec=120.0
            ):
                content = messages[-1]["content"] if messages else ""
                if "JSON array" in content or "diverse search query" in content.lower():
                    from app.services.llm import LLMResponse

                    return LLMResponse(
                        content='["What is FlexSearch?", "FlexSearch overview"]',
                        input_tokens=1,
                        output_tokens=1,
                        model="fake-model",
                        provider="test",
                        latency_ms=1,
                    )
                return await super().complete(
                    messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    timeout_sec=timeout_sec,
                )

        monkeypatch.setattr(
            "app.rag.chat.orchestrator.create_pipeline",
            lambda config=None, rag_mode=None: CountingPipeline(),
        )
        monkeypatch.setattr(
            "app.rag.chat.orchestrator.get_llm_service",
            lambda: StageLLM(),
        )

        response = await async_client.post(
            "/api/chat/stream",
            json={"project_id": project_id, "query": "What is FlexSearch?"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        body = response.text
        assert "event: debug" in body
        assert CountingPipeline.calls >= 2
        assert "event: done" in body


class TestChatConfigOptions:
    async def test_rag_options_include_chat(
        self, async_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        token = await create_user_and_login(async_client, "chat-opts@example.com")
        response = await async_client.get(
            "/api/rag/options",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert "chat" in data
        assert "defaults" in data["chat"]
        assert "prompts" in data["chat"]
        assert "system" in data["chat"]["prompts"]
        assert "answer" in data["chat"]["prompts"]
        assert "chat" in data["defaults"]
