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
from app.rag.ingestion import OCRExtractionStrategy, VLMExtractionStrategy
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
    GraphExtractionConfig,
    GraphRagConfig,
    GraphRetrievalConfig,
    RerankingConfig,
    RetrievalConfig,
    VectorRagConfig,
)


def build_extraction_strategy(
    config: ExtractionConfig | GraphExtractionConfig,
) -> BaseExtractionStrategy:
    if config.strategy == "vlm":
        return VLMExtractionStrategy()
    return OCRExtractionStrategy()


def build_chunking_strategy(config: ChunkingConfig) -> BaseChunkingStrategy:
    params = config.resolved_params()
    match config.strategy:
        case "recursive":
            return RecursiveChunking(
                chunk_size=params.chunk_size,
                overlap=params.overlap,
            )
        case "semantic":
            return SemanticChunking(
                similarity_threshold=params.similarity_threshold,
                min_chunk_size=params.min_chunk_size,
                max_chunk_size=params.max_chunk_size,
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
) -> BaseRetrievalStrategy:
    if rag_mode == RagMode.GRAPH:
        raise ValueError("Use build_graph_retrieval_strategy for graph projects")
    params = config.resolved_params()
    match config.strategy:
        case "parent_child":
            return ParentChildRetrieval()
        case "hybrid":
            return HybridRetrieval(rrf_k=params.rrf_k)
        case "bm25":
            return SparseRetrieval(k1=params.k1, b=params.b)
        case _:
            return DenseRetrieval(score_threshold=params.score_threshold)


def build_graph_retrieval_strategy(
    config: GraphRagConfig | GraphRetrievalConfig,
) -> BaseRetrievalStrategy:
    if isinstance(config, GraphRagConfig):
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
    return (
        extraction,
        build_chunking_strategy(rag_config.chunking),
        build_retrieval_strategy(rag_config.retrieval, rag_mode=rag_mode),
        build_reranking_strategy(rag_config.reranking, rag_mode=rag_mode),
    )
