"""
FlexSearch Backend - Per-project RAG configuration schemas.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.config import Settings, settings
from app.db.models import RagMode

VectorRetrievalStrategy = Literal["dense", "parent_child", "hybrid", "bm25"]
GraphRetrievalStrategy = Literal["graph_local", "graph_global"]
GraphBackend = Literal["neo4j", "microsoft"]
AllRetrievalStrategy = Union[VectorRetrievalStrategy, GraphRetrievalStrategy]


class PreprocessConfig(BaseModel):
    """Post-extract text cleanup (Phase 3)."""

    enabled: bool = True
    fix_encoding: bool = True
    normalize_whitespace: bool = True
    strip_headers_footers: bool = True


class ExtractionConfig(BaseModel):
    strategy: Literal["ocr", "vlm", "docling", "hybrid_pdf"] = "ocr"
    preprocess: PreprocessConfig = Field(default_factory=PreprocessConfig)
    extract_hierarchy: bool = Field(
        default=True,
        description="Attach heading_path / section_title metadata to chunks",
    )


class FixedWindowChunkingParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chunk_size: int = Field(default=512, ge=64, le=8192)
    overlap: int = Field(default=50, ge=0, le=1024)

    @model_validator(mode="after")
    def validate_overlap(self) -> "FixedWindowChunkingParams":
        if self.overlap >= self.chunk_size:
            raise ValueError("overlap must be smaller than chunk_size")
        return self


class RecursiveChunkingParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chunk_size: int = Field(default=512, ge=64, le=8192)
    overlap: int = Field(default=50, ge=0, le=1024)
    preserve_structure: bool = Field(
        default=True,
        description="Keep markdown tables/code fences as atomic units",
    )

    @model_validator(mode="after")
    def validate_overlap(self) -> "RecursiveChunkingParams":
        if self.overlap >= self.chunk_size:
            raise ValueError("overlap must be smaller than chunk_size")
        return self


class SemanticChunkingParams(BaseModel):
    """
    LangChain ``SemanticChunker`` params.

    ``similarity_threshold`` maps to breakpoint distance sensitivity
    (higher → fewer breaks). Optional ``breakpoint_threshold_type`` selects
    the LangChain breakpoint strategy.
    """

    model_config = ConfigDict(extra="forbid")

    similarity_threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    min_chunk_size: int = Field(default=100, ge=32, le=4096)
    max_chunk_size: int = Field(default=1000, ge=128, le=16384)
    breakpoint_threshold_type: Literal[
        "percentile", "standard_deviation", "interquartile", "gradient"
    ] = "percentile"
    buffer_size: int = Field(default=1, ge=1, le=10)

    @model_validator(mode="after")
    def validate_sizes(self) -> "SemanticChunkingParams":
        if self.min_chunk_size > self.max_chunk_size:
            raise ValueError("min_chunk_size must not exceed max_chunk_size")
        return self


class ParentChildChunkingParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    parent_chunk_size: int = Field(default=1500, ge=256, le=16384)
    child_chunk_size: int = Field(default=300, ge=64, le=4096)
    overlap: int = Field(default=50, ge=0, le=1024)

    @model_validator(mode="after")
    def validate_sizes(self) -> "ParentChildChunkingParams":
        if self.child_chunk_size > self.parent_chunk_size:
            raise ValueError("child_chunk_size must not exceed parent_chunk_size")
        if self.overlap >= self.child_chunk_size:
            raise ValueError("overlap must be smaller than child_chunk_size")
        return self


class ChunkingConfig(BaseModel):
    strategy: Literal["fixed_window", "recursive", "semantic", "parent_child"] = (
        "fixed_window"
    )
    params: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_strategy_params(self) -> "ChunkingConfig":
        self.resolved_params()
        return self

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


HierarchyRetrievalMode = Literal["chunks_only", "summaries_first", "mixed"]


class HierarchicalSummaryConfig(BaseModel):
    """
    Hierarchical summaries for vector mode (Phase 3).

    OpenSearch is the retrieval source of truth (``summary_level`` +
    ``member_chunk_ids``). Skipped automatically for Microsoft GraphRAG projects.
    """

    enabled: bool = True
    retrieval_mode: HierarchyRetrievalMode = "chunks_only"
    min_chunks: int = Field(
        default=6,
        ge=2,
        le=10_000,
        description="Skip summary job when document has fewer chunks",
    )
    n_clusters: int | None = Field(
        default=None,
        ge=2,
        le=200,
        description="K-Means cluster count; None → auto (≈ sqrt(n_chunks))",
    )
    cluster_max_tokens: int = Field(default=512, ge=64, le=4096)
    manifesto_max_tokens: int = Field(default=1024, ge=128, le=8192)


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


class ChatMemoryConfig(BaseModel):
    """Short-term conversational memory for chat context."""

    enabled: bool = True
    max_turns: int = Field(default=10, ge=1, le=50)
    ttl_seconds: int = Field(default=3600, ge=60, le=86400)


class ChatOptimizationConfig(BaseModel):
    """Query rewrite / keyword optimize / clarify before retrieval."""

    enabled: bool = False
    rewrite: bool = False
    clarify: bool = False


class ChatMultiQueryConfig(BaseModel):
    """Generate query variants and fuse retrieval results by consensus."""

    enabled: bool = False
    count: int = Field(default=3, ge=2, le=8)


class ChatMultihopConfig(BaseModel):
    """Decompose complex questions into sub-questions (hops)."""

    enabled: bool = False
    max_hops: int = Field(default=2, ge=1, le=5)


class ChatConfig(BaseModel):
    """Per-project chat / answer generation settings."""

    temperature: float = Field(default=0.3, ge=0.0, le=2.0)
    max_tokens: int = Field(default=2048, ge=64, le=8192)
    top_k: int = Field(default=5, ge=1, le=50)
    include_history: bool = True
    # Optional quality stages (orchestrator reads them; most off by default)
    context_window: int = Field(
        default=0,
        ge=0,
        le=5,
        description="Include ±N neighboring chunks around each hit (by chunk_index).",
    )
    memory: ChatMemoryConfig = Field(default_factory=ChatMemoryConfig)
    optimization: ChatOptimizationConfig = Field(default_factory=ChatOptimizationConfig)
    multi_query: ChatMultiQueryConfig = Field(default_factory=ChatMultiQueryConfig)
    multihop: ChatMultihopConfig = Field(default_factory=ChatMultihopConfig)
    debug: bool = False


class VectorRagConfig(BaseModel):
    extraction: ExtractionConfig = Field(default_factory=ExtractionConfig)
    chunking: ChunkingConfig = Field(default_factory=ChunkingConfig)
    retrieval: RetrievalConfig = Field(default_factory=RetrievalConfig)
    reranking: RerankingConfig = Field(default_factory=RerankingConfig)
    summaries: HierarchicalSummaryConfig = Field(
        default_factory=HierarchicalSummaryConfig
    )
    chat: ChatConfig = Field(default_factory=ChatConfig)

    @classmethod
    def from_settings(cls, s: Settings | None = None) -> VectorRagConfig:
        s = s or settings
        return cls(
            extraction=ExtractionConfig(strategy=s.extraction_strategy),
            chunking=ChunkingConfig(strategy=s.chunking_strategy, params={}),
            retrieval=RetrievalConfig(strategy=s.retrieval_strategy, params={}),
            reranking=RerankingConfig(strategy=s.reranking_strategy, params={}),
            summaries=HierarchicalSummaryConfig(),
            chat=ChatConfig(),
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
    strategy: Literal["ocr", "vlm", "docling", "hybrid_pdf"] = "ocr"
    passage_chunk_size: int = Field(default=800, ge=200, le=4096)
    preprocess: PreprocessConfig = Field(default_factory=PreprocessConfig)


class GraphIndexingConfig(BaseModel):
    """Neo4j per-passage entity extraction settings."""

    max_entities_per_passage: int = Field(default=20, ge=1, le=100)
    embed_entities: bool = Field(default=True)


class MicrosoftGraphIndexingConfig(BaseModel):
    enabled: bool = True
    method: Literal["standard", "nlp"] = "standard"
    community_level: int = Field(default=2, ge=0, le=4)


class GraphLocalRetrievalParams(BaseModel):
    """Neo4j graph_local params."""

    max_hops: int = Field(default=2, ge=1, le=5)
    top_entities: int = Field(default=10, ge=1, le=50)


class GraphGlobalRetrievalParams(BaseModel):
    """Neo4j graph_global params."""

    top_passages: int = Field(default=5, ge=1, le=50)


class MicrosoftGraphLocalRetrievalParams(BaseModel):
    community_level: int = Field(default=2, ge=0, le=4)
    max_context_tokens: int = Field(default=12000, ge=1000, le=50000)


class MicrosoftGraphGlobalRetrievalParams(BaseModel):
    community_level: int = Field(default=2, ge=0, le=4)
    dynamic_community_selection: bool = False
    max_context_tokens: int = Field(default=12000, ge=1000, le=50000)


class GraphRetrievalConfig(BaseModel):
    strategy: GraphRetrievalStrategy = "graph_local"
    params: dict[str, Any] = Field(default_factory=dict)

    def resolved_params(self, graph_backend: GraphBackend = "neo4j") -> BaseModel:
        if graph_backend == "microsoft":
            if self.strategy == "graph_global":
                return MicrosoftGraphGlobalRetrievalParams.model_validate(self.params)
            return MicrosoftGraphLocalRetrievalParams.model_validate(self.params)
        if self.strategy == "graph_global":
            return GraphGlobalRetrievalParams.model_validate(self.params)
        return GraphLocalRetrievalParams.model_validate(self.params)


class GraphRagConfig(BaseModel):
    graph_backend: GraphBackend = "neo4j"
    extraction: GraphExtractionConfig = Field(default_factory=GraphExtractionConfig)
    indexing: GraphIndexingConfig = Field(default_factory=GraphIndexingConfig)
    microsoft_indexing: MicrosoftGraphIndexingConfig = Field(
        default_factory=MicrosoftGraphIndexingConfig
    )
    retrieval: GraphRetrievalConfig = Field(default_factory=GraphRetrievalConfig)
    chat: ChatConfig = Field(default_factory=ChatConfig)

    @classmethod
    def from_settings(
        cls,
        s: Settings | None = None,
        *,
        graph_backend: GraphBackend = "neo4j",
    ) -> GraphRagConfig:
        s = s or settings
        return cls(
            graph_backend=graph_backend,
            extraction=GraphExtractionConfig(strategy=s.extraction_strategy),
            microsoft_indexing=MicrosoftGraphIndexingConfig(
                community_level=s.graphrag_community_level,
            ),
            retrieval=GraphRetrievalConfig(
                strategy="graph_local",
                params=(
                    {"community_level": s.graphrag_community_level}
                    if graph_backend == "microsoft"
                    else {}
                ),
            ),
            chat=ChatConfig(),
        )

    @classmethod
    def from_db(cls, data: dict[str, Any] | None) -> GraphRagConfig:
        if not data:
            return cls.from_settings()
        return cls.model_validate(data)

    def to_db(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    def ingestion_fingerprint(self) -> str:
        payload: dict[str, Any] = {
            "graph_backend": self.graph_backend,
            "extraction": self.extraction.model_dump(mode="json"),
            "indexing": self.indexing.model_dump(mode="json"),
        }
        if self.graph_backend == "microsoft":
            payload["microsoft_indexing"] = self.microsoft_indexing.model_dump(
                mode="json"
            )
        return _stable_hash(payload)

    def graph_indexing_fingerprint(self) -> str:
        payload: dict[str, Any] = {
            "graph_backend": self.graph_backend,
            "extraction": self.extraction.model_dump(mode="json"),
        }
        if self.graph_backend == "microsoft":
            payload["microsoft_indexing"] = self.microsoft_indexing.model_dump(
                mode="json"
            )
        else:
            payload["indexing"] = self.indexing.model_dump(mode="json")
        return _stable_hash(payload)


def parse_rag_config(
    rag_mode: RagMode | str,
    data: dict[str, Any] | None,
) -> VectorRagConfig | GraphRagConfig:
    mode = rag_mode if isinstance(rag_mode, RagMode) else RagMode(rag_mode)
    if mode == RagMode.GRAPH:
        return GraphRagConfig.from_db(data)
    return VectorRagConfig.from_db(data)


def default_rag_config_for_mode(
    rag_mode: RagMode | str,
    *,
    graph_backend: GraphBackend = "neo4j",
) -> VectorRagConfig | GraphRagConfig:
    mode = rag_mode if isinstance(rag_mode, RagMode) else RagMode(rag_mode)
    if mode == RagMode.GRAPH:
        return GraphRagConfig.from_settings(graph_backend=graph_backend)
    return VectorRagConfig.from_settings()


def graph_backend_of(config: VectorRagConfig | GraphRagConfig) -> GraphBackend | None:
    if isinstance(config, GraphRagConfig):
        return config.graph_backend
    return None


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
    summaries: HierarchicalSummaryConfig = Field(
        default_factory=HierarchicalSummaryConfig
    )
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
            summaries=project.summaries.model_copy(deep=True),
            top_k=resolved_top_k,
        )


class GraphEffectiveRagConfig(BaseModel):
    """Merged graph project config with per-query retrieval overrides."""

    graph_backend: GraphBackend
    extraction: GraphExtractionConfig
    indexing: GraphIndexingConfig
    microsoft_indexing: MicrosoftGraphIndexingConfig
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
            graph_backend=project.graph_backend,
            extraction=project.extraction,
            indexing=project.indexing,
            microsoft_indexing=project.microsoft_indexing,
            retrieval=retrieval,
            top_k=resolved_top_k,
        )
