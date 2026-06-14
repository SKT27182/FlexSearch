"""
FlexSearch Backend - Per-project RAG configuration schemas.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal, Union

from pydantic import BaseModel, Field, field_validator

from app.core.config import Settings, settings
from app.db.models import RagMode

VectorRetrievalStrategy = Literal["dense", "parent_child", "hybrid", "bm25"]
GraphRetrievalStrategy = Literal["graph_local", "graph_global"]
AllRetrievalStrategy = Union[VectorRetrievalStrategy, GraphRetrievalStrategy]


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


class RetrievalConfig(BaseModel):
    strategy: VectorRetrievalStrategy = "dense"
    params: dict[str, Any] = Field(default_factory=dict)

    def resolved_params(self) -> BaseModel:
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


class VectorRagConfig(BaseModel):
    extraction: ExtractionConfig = Field(default_factory=ExtractionConfig)
    chunking: ChunkingConfig = Field(default_factory=ChunkingConfig)
    retrieval: RetrievalConfig = Field(default_factory=RetrievalConfig)
    reranking: RerankingConfig = Field(default_factory=RerankingConfig)

    @classmethod
    def from_settings(cls, s: Settings | None = None) -> VectorRagConfig:
        s = s or settings
        return cls(
            extraction=ExtractionConfig(strategy=s.extraction_strategy),
            chunking=ChunkingConfig(strategy=s.chunking_strategy, params={}),
            retrieval=RetrievalConfig(strategy=s.retrieval_strategy, params={}),
            reranking=RerankingConfig(strategy=s.reranking_strategy, params={}),
        )

    @classmethod
    def from_db(cls, data: dict[str, Any] | None) -> VectorRagConfig:
        if not data:
            return cls.from_settings()
        return cls.model_validate(data)

    def to_db(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    def ingestion_fingerprint(self) -> str:
        payload = {
            "extraction": self.extraction.model_dump(mode="json"),
            "chunking": self.chunking.model_dump(mode="json"),
        }
        return _stable_hash(payload)


# Backward-compatible alias
RagConfig = VectorRagConfig


class GraphExtractionConfig(BaseModel):
    strategy: Literal["ocr", "vlm"] = "ocr"
    passage_chunk_size: int = Field(default=800, ge=200, le=4096)


class GraphIndexingConfig(BaseModel):
    max_entities_per_passage: int = Field(default=20, ge=1, le=100)
    embed_entities: bool = Field(default=True)


class GraphLocalRetrievalParams(BaseModel):
    max_hops: int = Field(default=2, ge=1, le=5)
    top_entities: int = Field(default=10, ge=1, le=50)


class GraphGlobalRetrievalParams(BaseModel):
    top_passages: int = Field(default=5, ge=1, le=50)


class GraphRetrievalConfig(BaseModel):
    strategy: GraphRetrievalStrategy = "graph_local"
    params: dict[str, Any] = Field(default_factory=dict)

    def resolved_params(self) -> BaseModel:
        if self.strategy == "graph_global":
            return GraphGlobalRetrievalParams.model_validate(self.params)
        return GraphLocalRetrievalParams.model_validate(self.params)


class GraphRagConfig(BaseModel):
    extraction: GraphExtractionConfig = Field(default_factory=GraphExtractionConfig)
    indexing: GraphIndexingConfig = Field(default_factory=GraphIndexingConfig)
    retrieval: GraphRetrievalConfig = Field(default_factory=GraphRetrievalConfig)

    @classmethod
    def from_settings(cls, s: Settings | None = None) -> GraphRagConfig:
        s = s or settings
        return cls(
            extraction=GraphExtractionConfig(strategy=s.extraction_strategy),
        )

    @classmethod
    def from_db(cls, data: dict[str, Any] | None) -> GraphRagConfig:
        if not data:
            return cls.from_settings()
        return cls.model_validate(data)

    def to_db(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    def ingestion_fingerprint(self) -> str:
        payload = {
            "extraction": self.extraction.model_dump(mode="json"),
            "indexing": self.indexing.model_dump(mode="json"),
        }
        return _stable_hash(payload)


def parse_rag_config(
    rag_mode: RagMode | str,
    data: dict[str, Any] | None,
) -> VectorRagConfig | GraphRagConfig:
    mode = rag_mode if isinstance(rag_mode, RagMode) else RagMode(rag_mode)
    if mode == RagMode.GRAPH:
        return GraphRagConfig.from_db(data)
    return VectorRagConfig.from_db(data)


def default_rag_config_for_mode(rag_mode: RagMode | str) -> VectorRagConfig | GraphRagConfig:
    mode = rag_mode if isinstance(rag_mode, RagMode) else RagMode(rag_mode)
    if mode == RagMode.GRAPH:
        return GraphRagConfig.from_settings()
    return VectorRagConfig.from_settings()


VECTOR_RETRIEVAL_STRATEGIES: frozenset[str] = frozenset(
    {"dense", "parent_child", "hybrid", "bm25"}
)
GRAPH_RETRIEVAL_STRATEGIES: frozenset[str] = frozenset({"graph_local", "graph_global"})


def extraction_fingerprint(extraction: ExtractionConfig | GraphExtractionConfig) -> str:
    return _stable_hash(extraction.model_dump(mode="json"))


def _stable_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class RetrievalOverrides(BaseModel):
    retrieval_strategy: AllRetrievalStrategy | None = None
    reranking_strategy: Literal["none", "cross_encoder"] | None = None
    top_k: int | None = Field(default=None, ge=1, le=50)
    retrieval_params: dict[str, Any] = Field(default_factory=dict)
    reranking_params: dict[str, Any] = Field(default_factory=dict)

    @field_validator("retrieval_params", "reranking_params", mode="before")
    @classmethod
    def none_to_dict(cls, v: Any) -> dict[str, Any]:
        return v if v is not None else {}


class EffectiveRagConfig(BaseModel):
    """Merged vector project config with per-query retrieval overrides."""

    extraction: ExtractionConfig
    chunking: ChunkingConfig
    retrieval: RetrievalConfig
    reranking: RerankingConfig
    top_k: int = 5

    @classmethod
    def for_retrieval(
        cls,
        project: VectorRagConfig,
        overrides: RetrievalOverrides | None = None,
        *,
        top_k: int | None = None,
    ) -> EffectiveRagConfig:
        overrides = overrides or RetrievalOverrides()
        retrieval = project.retrieval.model_copy(deep=True)
        reranking = project.reranking.model_copy(deep=True)
        if overrides.retrieval_strategy is not None:
            if overrides.retrieval_strategy not in VECTOR_RETRIEVAL_STRATEGIES:
                raise ValueError("Invalid vector retrieval strategy override")
            retrieval.strategy = overrides.retrieval_strategy  # type: ignore[assignment]
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


class GraphEffectiveRagConfig(BaseModel):
    """Merged graph project config with per-query retrieval overrides."""

    extraction: GraphExtractionConfig
    indexing: GraphIndexingConfig
    retrieval: GraphRetrievalConfig
    top_k: int = 5

    @classmethod
    def for_retrieval(
        cls,
        project: GraphRagConfig,
        overrides: RetrievalOverrides | None = None,
        *,
        top_k: int | None = None,
    ) -> GraphEffectiveRagConfig:
        overrides = overrides or RetrievalOverrides()
        retrieval = project.retrieval.model_copy(deep=True)
        if overrides.retrieval_strategy is not None:
            if overrides.retrieval_strategy not in GRAPH_RETRIEVAL_STRATEGIES:
                raise ValueError("Invalid graph retrieval strategy override")
            retrieval.strategy = overrides.retrieval_strategy  # type: ignore[assignment]
            retrieval.params = dict(overrides.retrieval_params)
        elif overrides.retrieval_params:
            retrieval.params = {**retrieval.params, **overrides.retrieval_params}
        if overrides.top_k is not None:
            resolved_top_k = overrides.top_k
        elif top_k is not None:
            resolved_top_k = top_k
        else:
            resolved_top_k = 5
        return cls(
            extraction=project.extraction,
            indexing=project.indexing,
            retrieval=retrieval,
            top_k=resolved_top_k,
        )
