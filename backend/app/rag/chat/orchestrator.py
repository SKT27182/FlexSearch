"""
Chat orchestrator: query stages → retrieve → context expand → generate → citations.

Stages (per-project ChatConfig) wrap RAGPipeline.retrieve() without forking it:
rewrite / optimize / clarify, multi-query consensus, multi-hop, neighbor expand, debug.
"""

from __future__ import annotations

import json
import time
from typing import Any, AsyncGenerator
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Project, RagMode
from app.prompts import render_prompt
from app.rag.chat.stages import (
    StageTimer,
    analyze_and_decompose,
    clarify_question,
    expand_neighbors,
    frequency_consensus_fuse,
    generate_multi_queries,
    optimize_keywords,
    rewrite_query,
)
from app.rag.chat.types import (
    ChatAnswer,
    ChatTurnMemory,
    build_citations,
    format_passages,
)
from app.rag.pipeline import create_pipeline
from app.rag.retrieval.base import RetrievalResult
from app.observability.metrics import metrics
from app.schemas.rag_config import (
    ChatConfig,
    GraphRagConfig,
    RetrievalOverrides,
    VectorRagConfig,
    parse_rag_config,
)
from app.services.chat_history import ChatHistoryService
from app.services.llm import get_llm_service
from app.services.session_memory import SessionMemoryService
from app.utils.logger import create_logger

logger = create_logger(__name__)


class ChatOrchestrator:
    """End-to-end RAG chat for vector and graph projects."""

    def __init__(
        self,
        db: AsyncSession,
        project: Project,
        *,
        memory: SessionMemoryService | None = None,
    ) -> None:
        self.db = db
        self.project = project
        rag_mode = project.rag_mode
        if isinstance(rag_mode, str):
            rag_mode = RagMode(rag_mode)
        self.rag_mode = rag_mode
        self.rag_config: VectorRagConfig | GraphRagConfig = parse_rag_config(
            rag_mode, project.rag_config
        )
        self.chat_config: ChatConfig = getattr(self.rag_config, "chat", None) or ChatConfig()
        self.memory = memory or SessionMemoryService()
        self.llm = get_llm_service()

    async def _load_history(
        self, session_id: UUID | None
    ) -> list[ChatTurnMemory]:
        if not session_id or not self.chat_config.include_history:
            return []
        if not self.chat_config.memory.enabled:
            return []

        max_turns = self.chat_config.memory.max_turns
        turns = await self.memory.get_turns(session_id, max_turns=max_turns)

        # Hydrate from Postgres when Redis misses (Phase 1 review note)
        if not turns:
            db_turns = await ChatHistoryService(self.db).turns_as_memory(
                session_id, max_turns=max_turns
            )
            if db_turns:
                await self.memory.replace_turns(
                    session_id,
                    db_turns,
                    ttl_seconds=self.chat_config.memory.ttl_seconds,
                )
                turns = db_turns
                logger.info(
                    "Hydrated session memory from Postgres session_id=%s turns=%d",
                    session_id,
                    len(db_turns),
                )

        return [ChatTurnMemory(role=t["role"], content=t["content"]) for t in turns]

    def _graph_aware_overrides(
        self, overrides: RetrievalOverrides | None
    ) -> RetrievalOverrides | None:
        """For graph + multihop: nudge local retrieval max_hops when unset."""
        if self.rag_mode != RagMode.GRAPH or not self.chat_config.multihop.enabled:
            return overrides
        ov = overrides.model_copy(deep=True) if overrides else RetrievalOverrides()
        params = dict(ov.retrieval_params or {})
        if "max_hops" not in params:
            params["max_hops"] = self.chat_config.multihop.max_hops
            ov.retrieval_params = params
        return ov

    async def _pipeline_retrieve(
        self,
        query: str,
        *,
        top_k: int,
        overrides: RetrievalOverrides | None,
    ) -> tuple[list[RetrievalResult], str, str]:
        pipeline = create_pipeline(self.rag_config, rag_mode=self.rag_mode)
        return await pipeline.retrieve(
            query=query,
            project_id=str(self.project.id),
            top_k=top_k,
            overrides=overrides,
        )

    async def _prepare_query(
        self,
        question: str,
        history: list[ChatTurnMemory],
        timer: StageTimer,
    ) -> tuple[str, str | None]:
        """
        Rewrite / optimize / clarify.

        Returns ``(retrieval_query, clarify_response_or_none)``.
        When clarify returns a string, the caller should short-circuit without retrieve.
        """
        query = question
        opt = self.chat_config.optimization

        if opt.enabled and opt.clarify:
            timer.start("clarify")
            clarifying = await clarify_question(self.llm, question, history)
            timer.end("clarify", asked=bool(clarifying))
            if clarifying:
                return question, clarifying

        if opt.enabled and opt.rewrite and (
            history or self.chat_config.memory.enabled
        ):
            timer.start("rewrite")
            query = await rewrite_query(self.llm, query, history)
            timer.end("rewrite", rewritten=query != question)

        if opt.enabled:
            # Keyword optimize when rewrite flag is off but optimization is on,
            # or always as a light lexical assist when rewrite ran.
            timer.start("optimize")
            query = await optimize_keywords(self.llm, query)
            timer.end("optimize", changed=query != question)

        return query, None

    async def _retrieve_staged(
        self,
        query: str,
        *,
        top_k: int,
        overrides: RetrievalOverrides | None,
        timer: StageTimer,
    ) -> tuple[list[RetrievalResult], str, str, dict[str, Any]]:
        """Multi-hop / multi-query / single retrieve + optional neighbor expand."""
        effective_overrides = self._graph_aware_overrides(overrides)
        stage_meta: dict[str, Any] = {"queries": [query]}
        results: list[RetrievalResult]
        retrieval_name: str
        rerank_name: str

        if self.chat_config.multihop.enabled:
            timer.start("multihop_analyze")
            needed, hops = await analyze_and_decompose(
                self.llm,
                query,
                max_hops=self.chat_config.multihop.max_hops,
                graph_aware=self.rag_mode == RagMode.GRAPH,
            )
            timer.end("multihop_analyze", needed=needed, hop_count=len(hops))
            stage_meta["multihop"] = {"needed": needed, "hops": hops}

            if needed and len(hops) > 1:
                timer.start("multihop_retrieve")
                lists: list[list[RetrievalResult]] = []
                names: list[str] = []
                reranks: list[str] = []
                for hop_q in hops:
                    hop_results, rname, rrname = await self._pipeline_retrieve(
                        hop_q, top_k=top_k, overrides=effective_overrides
                    )
                    lists.append(hop_results)
                    names.append(rname)
                    reranks.append(rrname)
                results = frequency_consensus_fuse(lists, top_k=top_k)
                retrieval_name = names[0] if names else "dense"
                rerank_name = reranks[0] if reranks else "none"
                stage_meta["queries"] = hops
                timer.end(
                    "multihop_retrieve",
                    hops=len(hops),
                    fused=len(results),
                    retrieval=retrieval_name,
                )
            else:
                timer.start("retrieve")
                results, retrieval_name, rerank_name = await self._pipeline_retrieve(
                    query, top_k=top_k, overrides=effective_overrides
                )
                timer.end(
                    "retrieve",
                    hits=len(results),
                    retrieval=retrieval_name,
                    reranking=rerank_name,
                )

        elif self.chat_config.multi_query.enabled:
            timer.start("multi_query_generate")
            queries = await generate_multi_queries(
                self.llm,
                query,
                count=self.chat_config.multi_query.count,
            )
            timer.end("multi_query_generate", count=len(queries))
            stage_meta["queries"] = queries

            timer.start("multi_query_retrieve")
            lists = []
            names = []
            reranks = []
            for q in queries:
                q_results, rname, rrname = await self._pipeline_retrieve(
                    q, top_k=top_k, overrides=effective_overrides
                )
                lists.append(q_results)
                names.append(rname)
                reranks.append(rrname)
            results = frequency_consensus_fuse(lists, top_k=top_k)
            retrieval_name = names[0] if names else "dense"
            rerank_name = reranks[0] if reranks else "none"
            timer.end(
                "multi_query_retrieve",
                queries=len(queries),
                fused=len(results),
                retrieval=retrieval_name,
            )

        else:
            timer.start("retrieve")
            results, retrieval_name, rerank_name = await self._pipeline_retrieve(
                query, top_k=top_k, overrides=effective_overrides
            )
            timer.end(
                "retrieve",
                hits=len(results),
                retrieval=retrieval_name,
                reranking=rerank_name,
            )

        if self.chat_config.context_window > 0 and self.rag_mode == RagMode.VECTOR:
            timer.start("context_expand")
            before = len(results)
            results = await expand_neighbors(
                results,
                project_id=str(self.project.id),
                context_window=self.chat_config.context_window,
            )
            timer.end(
                "context_expand",
                window=self.chat_config.context_window,
                before=before,
                after=len(results),
            )

        stage_meta["retrieval_strategy"] = retrieval_name
        stage_meta["reranking_strategy"] = rerank_name
        return results, retrieval_name, rerank_name, stage_meta

    def _build_messages(
        self,
        question: str,
        citations: list,
        history: list[ChatTurnMemory],
    ) -> list[dict[str, str]]:
        system = render_prompt(
            "system",
            project_name=self.project.name,
            rag_mode=self.rag_mode.value,
        )
        history_dicts = [{"role": h.role, "content": h.content} for h in history]
        user = render_prompt(
            "answer",
            question=question,
            passages=format_passages(citations),
            history=history_dicts,
        )
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]

    async def answer(
        self,
        question: str,
        *,
        session_id: UUID | None = None,
        top_k: int | None = None,
        overrides: RetrievalOverrides | None = None,
    ) -> ChatAnswer:
        start = time.time()
        timer = StageTimer(enabled=True)
        history = await self._load_history(session_id)
        effective_top_k = top_k or self.chat_config.top_k
        debug_payload = None

        query, clarifying = await self._prepare_query(question, history, timer)
        if clarifying:
            if self.chat_config.debug:
                debug_payload = timer.summary()
            answer = ChatAnswer(
                answer=clarifying,
                citations=[],
                retrieval_strategy="clarify",
                reranking_strategy="none",
                session_id=str(session_id) if session_id else None,
                empty_retrieval=True,
                latency_ms=int((time.time() - start) * 1000),
                model=self.llm.model_name,
                debug=debug_payload,
            )
            self._record_metrics("query", answer)
            self._record_stage_timings(timer)
            return answer

        results, retrieval_name, rerank_name, _meta = await self._retrieve_staged(
            query, top_k=effective_top_k, overrides=overrides, timer=timer
        )
        citations = build_citations(results)
        empty = len(citations) == 0

        if empty:
            answer_text = (
                "I could not find relevant information in the project knowledge base "
                "for that question."
            )
            if self.chat_config.debug:
                debug_payload = timer.summary()
            answer = ChatAnswer(
                answer=answer_text,
                citations=[],
                retrieval_strategy=retrieval_name,
                reranking_strategy=rerank_name,
                session_id=str(session_id) if session_id else None,
                empty_retrieval=True,
                latency_ms=int((time.time() - start) * 1000),
                model=self.llm.model_name,
                debug=debug_payload,
            )
            self._record_metrics("query", answer)
            self._record_stage_timings(timer)
            return answer

        timer.start("generate")
        messages = self._build_messages(question, citations, history)
        response = await self.llm.complete(
            messages,
            temperature=self.chat_config.temperature,
            max_tokens=self.chat_config.max_tokens,
        )
        timer.end("generate", model=response.model)

        if self.chat_config.debug:
            debug_payload = timer.summary()
        answer = ChatAnswer(
            answer=response.content,
            citations=citations,
            retrieval_strategy=retrieval_name,
            reranking_strategy=rerank_name,
            session_id=str(session_id) if session_id else None,
            model=response.model,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            latency_ms=int((time.time() - start) * 1000),
            empty_retrieval=False,
            debug=debug_payload,
        )
        self._record_metrics("query", answer)
        self._record_stage_timings(timer)
        return answer

    def _record_metrics(self, path: str, answer: ChatAnswer) -> None:
        metrics.record_chat(
            path=path,
            empty_retrieval=answer.empty_retrieval,
            latency_ms=answer.latency_ms,
            input_tokens=answer.input_tokens,
            output_tokens=answer.output_tokens,
            rag_mode=self.rag_mode.value,
        )

    def _record_stage_timings(self, timer: StageTimer) -> None:
        for event in timer.events:
            metrics.observe_stage(
                event.stage,
                event.duration_ms / 1000.0,
                rag_mode=self.rag_mode.value,
            )

    async def stream(
        self,
        question: str,
        *,
        session_id: UUID | None = None,
        top_k: int | None = None,
        overrides: RetrievalOverrides | None = None,
    ) -> AsyncGenerator[tuple[str, dict[str, Any]], None]:
        """Yield (event_name, payload) for SSE."""
        start = time.time()
        # Always collect stage timings for metrics; expose via SSE when debug on
        timer = StageTimer(enabled=True)
        history = await self._load_history(session_id)
        effective_top_k = top_k or self.chat_config.top_k
        debug_on = self.chat_config.debug

        yield ("status", {"stage": "prepare"})
        query, clarifying = await self._prepare_query(question, history, timer)
        if debug_on:
            for event in timer.events:
                yield ("debug", event.to_dict())

        if clarifying:
            yield ("token", {"content": clarifying})
            done_payload: dict[str, Any] = {
                "answer": clarifying,
                "empty_retrieval": True,
                "retrieval_strategy": "clarify",
                "reranking_strategy": "none",
                "session_id": str(session_id) if session_id else None,
                "latency_ms": int((time.time() - start) * 1000),
                "model": self.llm.model_name,
            }
            if debug_on:
                done_payload["debug"] = timer.summary()
                yield ("debug", {"stage": "summary", **timer.summary()})
            metrics.record_chat(
                path="stream",
                empty_retrieval=True,
                latency_ms=done_payload["latency_ms"],
                rag_mode=self.rag_mode.value,
            )
            self._record_stage_timings(timer)
            yield ("done", done_payload)
            return

        yield ("status", {"stage": "retrieve"})
        results, retrieval_name, rerank_name, stage_meta = await self._retrieve_staged(
            query, top_k=effective_top_k, overrides=overrides, timer=timer
        )
        if debug_on:
            # Emit only new events since prepare
            for event in timer.events:
                if event.stage in (
                    "retrieve",
                    "multi_query_generate",
                    "multi_query_retrieve",
                    "multihop_analyze",
                    "multihop_retrieve",
                    "context_expand",
                ):
                    yield ("debug", event.to_dict())

        citations = build_citations(results)
        yield (
            "citations",
            {
                "citations": [c.to_dict() for c in citations],
                "retrieval_strategy": retrieval_name,
                "reranking_strategy": rerank_name,
                "queries": stage_meta.get("queries"),
            },
        )

        if not citations:
            answer_text = (
                "I could not find relevant information in the project knowledge base "
                "for that question."
            )
            yield ("token", {"content": answer_text})
            done_payload = {
                "answer": answer_text,
                "empty_retrieval": True,
                "retrieval_strategy": retrieval_name,
                "reranking_strategy": rerank_name,
                "session_id": str(session_id) if session_id else None,
                "latency_ms": int((time.time() - start) * 1000),
                "model": self.llm.model_name,
            }
            if debug_on:
                done_payload["debug"] = timer.summary()
                yield ("debug", {"stage": "summary", **timer.summary()})
            metrics.record_chat(
                path="stream",
                empty_retrieval=True,
                latency_ms=done_payload["latency_ms"],
                rag_mode=self.rag_mode.value,
            )
            self._record_stage_timings(timer)
            yield ("done", done_payload)
            return

        yield ("status", {"stage": "generate"})
        timer.start("generate")
        messages = self._build_messages(question, citations, history)
        full_parts: list[str] = []
        input_tokens = 0
        output_tokens = 0
        model = self.llm.model_name

        async for chunk in self.llm.stream(
            messages,
            temperature=self.chat_config.temperature,
            max_tokens=self.chat_config.max_tokens,
        ):
            if chunk.get("type") == "token":
                text = chunk.get("content") or ""
                if text:
                    full_parts.append(text)
                    yield ("token", {"content": text})
            elif chunk.get("type") == "usage":
                input_tokens = int(chunk.get("input_tokens") or 0)
                output_tokens = int(chunk.get("output_tokens") or 0)
                model = chunk.get("model") or model

        timer.end("generate", model=model)
        if debug_on:
            gen_events = [e for e in timer.events if e.stage == "generate"]
            if gen_events:
                yield ("debug", gen_events[-1].to_dict())

        answer_text = "".join(full_parts)
        done_payload = {
            "answer": answer_text,
            "empty_retrieval": False,
            "retrieval_strategy": retrieval_name,
            "reranking_strategy": rerank_name,
            "session_id": str(session_id) if session_id else None,
            "latency_ms": int((time.time() - start) * 1000),
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "citations": [c.to_dict() for c in citations],
        }
        if debug_on:
            done_payload["debug"] = timer.summary()
            yield ("debug", {"stage": "summary", **timer.summary()})
        metrics.record_chat(
            path="stream",
            empty_retrieval=False,
            latency_ms=done_payload["latency_ms"],
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            rag_mode=self.rag_mode.value,
        )
        self._record_stage_timings(timer)
        yield ("done", done_payload)

    async def persist_turn_memory(
        self,
        session_id: UUID,
        *,
        question: str,
        answer: str,
    ) -> None:
        if not self.chat_config.memory.enabled:
            return
        await self.memory.append_turn(
            session_id,
            role="user",
            content=question,
            ttl_seconds=self.chat_config.memory.ttl_seconds,
            max_turns=self.chat_config.memory.max_turns,
        )
        await self.memory.append_turn(
            session_id,
            role="assistant",
            content=answer,
            ttl_seconds=self.chat_config.memory.ttl_seconds,
            max_turns=self.chat_config.memory.max_turns,
        )


def format_sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"
