"""
FlexSearch Backend - Per-project RAG configuration schemas.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.core.config import Settings, settings
from app.db.models import RagMode

VectorRetrievalStrategy = Literal["dense", "parent_child", "hybrid", "bm25"]
GraphRetrievalStrategy = Literal["graph_local", "graph_global"]
RetrievalStrategy = VectorRetrievalStrategy | GraphRetrievalStrategy


class ExtractionConfig(BaseModel):
    strategy: Literal["ocr", "vlm"] = "ocr"


class FixedWindowChunkingParams(BaseModel):
    chunk_size: int = Field(default=512, ge=64, le=8192)
    overlap: int = Field(default=50, ge=0, le=1024)


class RecursiveChunkingParams(BaseModel):
    chunk_size: int = Field(default=512, ge=64, le=8192)
    overlap: int = Field(default=50, ge=0, le=1024)


class SemanticChunkingParams(BaseModel):
    similarity_threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    min_chunk_size: int = Field(default=100, ge=32, le=4096)
    max_chunk_size: int = Field(default=1000, ge=128, le=16384)


class ParentChildChunkingParams(BaseModel):
    parent_chunk_size: int = Field(default=1500, ge=256, le=16384)
    child_chunk_size: int = Field(default=300, ge=64, le=4096)
    overlap: int = Field(default=50, ge=0, le=1024)


class ChunkingConfig(BaseModel):
    strategy: Literal[
        "fixed_window", "recursive", "semantic", "parent_child"
    ] = "fixed_window"
    params: dict[str, Any] = Field(default_factory=dict)

    def resolved_params(self) -> BaseModel:
        match self.strategy:
            case "recursive":
                return RecursiveChunkingParams.model_validate(self.params)
            case "semantic":
                return SemanticChunkingParams.model_validate(self.params)
            case "parent_child":
                return ParentChildChunkingParams.model_validate(self.params)
            case _:
                return FixedWindowChunkingParams.model_validate(self.params)


class DenseRetrievalParams(BaseModel):
    score_threshold: float | None = Field(default=None, ge=0.0, le=1.0)


class HybridRetrievalParams(BaseModel):
    rrf_k: int = Field(default=60, ge=1, le=200)


class Bm25RetrievalParams(BaseModel):
    k1: float = Field(default=1.5, ge=0.1, le=3.0)
    b: float = Field(default=0.75, ge=0.0, le=1.0)


class GraphIndexingConfig(BaseModel):
    enabled: bool = True
    method: Literal["standard", "nlp"] = "standard"
    community_level: int = Field(default=2, ge=0, le=4)


class GraphLocalRetrievalParams(BaseModel):
    community_level: int = Field(default=2, ge=0, le=4)
    max_context_tokens: int = Field(default=12000, ge=1000, le=50000)


class GraphGlobalRetrievalParams(BaseModel):
    community_level: int = Field(default=2, ge=0, le=4)
    dynamic_community_selection: bool = False
    max_context_tokens: int = Field(default=12000, ge=1000, le=50000)


class GraphRetrievalConfig(BaseModel):
    strategy: GraphRetrievalStrategy = "graph_local"
    params: dict[str, Any] = Field(default_factory=dict)

    def resolved_params(self) -> BaseModel:
        if self.strategy == "graph_global":
            return GraphGlobalRetrievalParams.model_validate(self.params)
        return GraphLocalRetrievalParams.model_validate(self.params)


class RetrievalConfig(BaseModel):
    strategy: RetrievalStrategy = "dense"
    params: dict[str, Any] = Field(default_factory=dict)

    def resolved_params(self) -> BaseModel:
        if self.strategy == "graph_global":
            return GraphGlobalRetrievalParams.model_validate(self.params)
        if self.strategy == "graph_local":
            return GraphLocalRetrievalParams.model_validate(self.params)
        if self.strategy == "hybrid":
            return HybridRetrievalParams.model_validate(self.params)
        if self.strategy == "bm25":
            return Bm25RetrievalParams.model_validate(self.params)
        if self.strategy == "parent_child":
            return DenseRetrievalParams.model_validate(self.params)
        return DenseRetrievalParams.model_validate(self.params)


class RerankingConfig(BaseModel):
    strategy: Literal["none", "cross_encoder"] = "none"
    params: dict[str, Any] = Field(default_factory=dict)


class RagConfig(BaseModel):
    extraction: ExtractionConfig = Field(default_factory=ExtractionConfig)
    chunking: ChunkingConfig = Field(default_factory=ChunkingConfig)
    retrieval: RetrievalConfig = Field(default_factory=RetrievalConfig)
    reranking: RerankingConfig = Field(default_factory=RerankingConfig)
    graph_indexing: GraphIndexingConfig = Field(default_factory=GraphIndexingConfig)
    graph_retrieval: GraphRetrievalConfig = Field(default_factory=GraphRetrievalConfig)

    @classmethod
    def for_mode(cls, mode: RagMode, s: Settings | None = None) -> RagConfig:
        s = s or settings
        if mode == RagMode.GRAPH:
            return cls(
                extraction=ExtractionConfig(strategy=s.extraction_strategy),
                chunking=ChunkingConfig(strategy="fixed_window", params={}),
                retrieval=RetrievalConfig(
                    strategy="graph_local",
                    params={"community_level": s.graphrag_community_level},
                ),
                reranking=RerankingConfig(strategy="none", params={}),
                graph_indexing=GraphIndexingConfig(
                    enabled=True,
                    method="standard",
                    community_level=s.graphrag_community_level,
                ),
                graph_retrieval=GraphRetrievalConfig(
                    strategy="graph_local",
                    params={"community_level": s.graphrag_community_level},
                ),
            )
        return cls.from_settings(s)

    @classmethod
    def from_settings(cls, s: Settings | None = None) -> RagConfig:
        s = s or settings
        chunk_defaults: dict[str, Any] = {}
        retrieval_defaults: dict[str, Any] = {}
        rerank_defaults: dict[str, Any] = {}
        return cls(
            extraction=ExtractionConfig(strategy=s.extraction_strategy),
            chunking=ChunkingConfig(
                strategy=s.chunking_strategy,
                params=chunk_defaults,
            ),
            retrieval=RetrievalConfig(
                strategy=s.retrieval_strategy,
                params=retrieval_defaults,
            ),
            reranking=RerankingConfig(strategy=s.reranking_strategy, params=rerank_defaults),
        )

    @classmethod
    def from_db(cls, data: dict[str, Any] | None) -> RagConfig:
        if not data:
            return cls.from_settings()
        return cls.model_validate(data)

    def to_db(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    def ingestion_fingerprint(self) -> str:
        """Hash extraction + chunking (used to decide full vs partial reprocess)."""
        payload = {
            "extraction": self.extraction.model_dump(mode="json"),
            "chunking": self.chunking.model_dump(mode="json"),
        }
        return _stable_hash(payload)

    def graph_indexing_fingerprint(self) -> str:
        payload = {
            "graph_indexing": self.graph_indexing.model_dump(mode="json"),
            "extraction": self.extraction.model_dump(mode="json"),
        }
        return _stable_hash(payload)

    def is_graph_retrieval(self) -> bool:
        return self.retrieval.strategy in ("graph_local", "graph_global")

    def is_vector_retrieval(self) -> bool:
        return not self.is_graph_retrieval()


def extraction_fingerprint(extraction: ExtractionConfig) -> str:
    return _stable_hash(extraction.model_dump(mode="json"))


def _stable_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class RetrievalOverrides(BaseModel):
    retrieval_strategy: RetrievalStrategy | None = None
    reranking_strategy: Literal["none", "cross_encoder"] | None = None
    top_k: int | None = Field(default=None, ge=1, le=50)
    retrieval_params: dict[str, Any] = Field(default_factory=dict)
    reranking_params: dict[str, Any] = Field(default_factory=dict)

    @field_validator("retrieval_params", "reranking_params", mode="before")
    @classmethod
    def none_to_dict(cls, v: Any) -> dict[str, Any]:
        return v if v is not None else {}


class EffectiveRagConfig(BaseModel):
    """Merged project config with per-query retrieval overrides."""

    extraction: ExtractionConfig
    chunking: ChunkingConfig
    retrieval: RetrievalConfig
    reranking: RerankingConfig
    top_k: int = 5

    @classmethod
    def for_retrieval(
        cls,
        project: RagConfig,
        overrides: RetrievalOverrides | None = None,
        *,
        top_k: int | None = None,
    ) -> EffectiveRagConfig:
        overrides = overrides or RetrievalOverrides()
        retrieval = project.retrieval.model_copy(deep=True)
        reranking = project.reranking.model_copy(deep=True)
        if overrides.retrieval_strategy is not None:
            retrieval.strategy = overrides.retrieval_strategy
            retrieval.params = dict(overrides.retrieval_params)
        elif overrides.retrieval_params:
            retrieval.params = {**retrieval.params, **overrides.retrieval_params}
        if overrides.reranking_strategy is not None:
            reranking.strategy = overrides.reranking_strategy
            reranking.params = dict(overrides.reranking_params)
        elif overrides.reranking_params:
            reranking.params = {**reranking.params, **overrides.reranking_params}
        if overrides.top_k is not None:
            resolved_top_k = overrides.top_k
        elif top_k is not None:
            resolved_top_k = top_k
        else:
            resolved_top_k = 5
        return cls(
            extraction=project.extraction,
            chunking=project.chunking,
            retrieval=retrieval,
            reranking=reranking,
            top_k=resolved_top_k,
        )
