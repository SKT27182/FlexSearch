"""
Build RAG strategy instances from RagConfig.
"""

from app.db.models import RagMode
from app.rag.chunking import (
    BaseChunkingStrategy,
    FixedWindowChunking,
    ParentChildChunking,
    RecursiveChunking,
    SemanticChunking,
)
from app.rag.ingestion import (
    DoclingExtractionStrategy,
    HybridPdfExtractionStrategy,
    OCRExtractionStrategy,
    VLMExtractionStrategy,
)
from app.rag.ingestion.base import BaseExtractionStrategy
from app.rag.reranking import BaseRerankingStrategy, CrossEncoderReranking, NoReranking
from app.rag.retrieval import (
    BaseRetrievalStrategy,
    DenseRetrieval,
    GraphGlobalRetrieval,
    GraphLocalRetrieval,
    HybridRetrieval,
    ParentChildRetrieval,
    SparseRetrieval,
)
from app.schemas.rag_config import (
    ChunkingConfig,
    ExtractionConfig,
    GraphEffectiveRagConfig,
    GraphExtractionConfig,
    GraphRagConfig,
    GraphRetrievalConfig,
    HierarchyRetrievalMode,
    RerankingConfig,
    RetrievalConfig,
    VectorRagConfig,
)


def build_extraction_strategy(
    config: ExtractionConfig | GraphExtractionConfig,
) -> BaseExtractionStrategy:
    match config.strategy:
        case "vlm":
            return VLMExtractionStrategy()
        case "docling":
            return DoclingExtractionStrategy()
        case "hybrid_pdf":
            return HybridPdfExtractionStrategy()
        case _:
            return OCRExtractionStrategy()


def build_chunking_strategy(config: ChunkingConfig) -> BaseChunkingStrategy:
    params = config.resolved_params()
    match config.strategy:
        case "recursive":
            return RecursiveChunking(
                chunk_size=params.chunk_size,
                overlap=params.overlap,
                preserve_structure=getattr(params, "preserve_structure", True),
            )
        case "semantic":
            return SemanticChunking(
                similarity_threshold=params.similarity_threshold,
                min_chunk_size=params.min_chunk_size,
                max_chunk_size=params.max_chunk_size,
                breakpoint_threshold_type=getattr(
                    params, "breakpoint_threshold_type", "percentile"
                ),
                buffer_size=getattr(params, "buffer_size", 1),
            )
        case "parent_child":
            return ParentChildChunking(
                parent_chunk_size=params.parent_chunk_size,
                child_chunk_size=params.child_chunk_size,
                overlap=params.overlap,
            )
        case _:
            return FixedWindowChunking(
                chunk_size=params.chunk_size,
                overlap=params.overlap,
            )


def build_retrieval_strategy(
    config: RetrievalConfig,
    *,
    rag_mode: RagMode = RagMode.VECTOR,
    hierarchy_mode: HierarchyRetrievalMode = "chunks_only",
) -> BaseRetrievalStrategy:
    if rag_mode == RagMode.GRAPH:
        raise ValueError("Use build_graph_retrieval_strategy for graph projects")
    params = config.resolved_params()
    match config.strategy:
        case "parent_child":
            return ParentChildRetrieval(hierarchy_mode=hierarchy_mode)
        case "hybrid":
            return HybridRetrieval(rrf_k=params.rrf_k, hierarchy_mode=hierarchy_mode)
        case "bm25":
            return SparseRetrieval(
                k1=params.k1, b=params.b, hierarchy_mode=hierarchy_mode
            )
        case _:
            return DenseRetrieval(
                score_threshold=params.score_threshold,
                hierarchy_mode=hierarchy_mode,
            )


def build_graph_retrieval_strategy(
    config: GraphRagConfig | GraphEffectiveRagConfig | GraphRetrievalConfig,
) -> BaseRetrievalStrategy:
    if isinstance(config, (GraphRagConfig, GraphEffectiveRagConfig)):
        graph_backend = config.graph_backend
        retrieval = config.retrieval
    else:
        graph_backend = "neo4j"
        retrieval = config

    if retrieval.strategy == "graph_global":
        if graph_backend == "microsoft":
            params = retrieval.resolved_params("microsoft")
            return GraphGlobalRetrieval(
                graph_backend="microsoft",
                microsoft_params=params,  # type: ignore[arg-type]
            )
        return GraphGlobalRetrieval(
            graph_backend="neo4j",
            neo4j_config=retrieval,
        )

    if graph_backend == "microsoft":
        params = retrieval.resolved_params("microsoft")
        return GraphLocalRetrieval(
            graph_backend="microsoft",
            microsoft_params=params,  # type: ignore[arg-type]
        )
    return GraphLocalRetrieval(
        graph_backend="neo4j",
        neo4j_config=retrieval,
    )


def build_reranking_strategy(
    config: RerankingConfig,
    *,
    rag_mode: RagMode = RagMode.VECTOR,
) -> BaseRerankingStrategy:
    if rag_mode == RagMode.GRAPH:
        return NoReranking()
    if config.strategy == "cross_encoder":
        model_name = config.params.get(
            "model_name", "cross-encoder/ms-marco-MiniLM-L-6-v2"
        )
        return CrossEncoderReranking(model_name=model_name)
    return NoReranking()


def build_pipeline_strategies(
    rag_config: VectorRagConfig | GraphRagConfig,
    rag_mode: RagMode = RagMode.VECTOR,
) -> tuple[
    BaseExtractionStrategy,
    BaseChunkingStrategy | None,
    BaseRetrievalStrategy,
    BaseRerankingStrategy,
]:
    extraction = build_extraction_strategy(rag_config.extraction)
    if rag_mode == RagMode.GRAPH:
        assert isinstance(rag_config, GraphRagConfig)
        return (
            extraction,
            None,
            build_graph_retrieval_strategy(rag_config),
            NoReranking(),
        )
    assert isinstance(rag_config, VectorRagConfig)
    hierarchy_mode = rag_config.summaries.retrieval_mode
    return (
        extraction,
        build_chunking_strategy(rag_config.chunking),
        build_retrieval_strategy(
            rag_config.retrieval,
            rag_mode=rag_mode,
            hierarchy_mode=hierarchy_mode,
        ),
        build_reranking_strategy(rag_config.reranking, rag_mode=rag_mode),
    )
