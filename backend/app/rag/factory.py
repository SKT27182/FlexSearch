"""
Build RAG strategy instances from RagConfig.
"""

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
    HybridRetrieval,
    ParentChildRetrieval,
    SparseRetrieval,
)
from app.schemas.rag_config import (
    ChunkingConfig,
    ExtractionConfig,
    RagConfig,
    RerankingConfig,
    RetrievalConfig,
)


def build_extraction_strategy(config: ExtractionConfig) -> BaseExtractionStrategy:
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


def build_retrieval_strategy(config: RetrievalConfig) -> BaseRetrievalStrategy:
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


def build_reranking_strategy(config: RerankingConfig) -> BaseRerankingStrategy:
    if config.strategy == "cross_encoder":
        model_name = config.params.get(
            "model_name", "cross-encoder/ms-marco-MiniLM-L-6-v2"
        )
        return CrossEncoderReranking(model_name=model_name)
    return NoReranking()


def build_pipeline_strategies(rag_config: RagConfig) -> tuple[
    BaseExtractionStrategy,
    BaseChunkingStrategy,
    BaseRetrievalStrategy,
    BaseRerankingStrategy,
]:
    return (
        build_extraction_strategy(rag_config.extraction),
        build_chunking_strategy(rag_config.chunking),
        build_retrieval_strategy(rag_config.retrieval),
        build_reranking_strategy(rag_config.reranking),
    )
