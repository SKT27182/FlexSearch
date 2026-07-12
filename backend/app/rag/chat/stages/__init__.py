"""Query-quality stages wired around RAGPipeline.retrieve()."""

from app.rag.chat.stages.context_expand import expand_neighbors
from app.rag.chat.stages.debug import StageTimer, DebugEvent
from app.rag.chat.stages.fusion import frequency_consensus_fuse
from app.rag.chat.stages.multi_query import generate_multi_queries
from app.rag.chat.stages.multihop import analyze_and_decompose
from app.rag.chat.stages.rewrite import (
    clarify_question,
    optimize_keywords,
    rewrite_query,
)

__all__ = [
    "expand_neighbors",
    "StageTimer",
    "DebugEvent",
    "frequency_consensus_fuse",
    "generate_multi_queries",
    "analyze_and_decompose",
    "clarify_question",
    "optimize_keywords",
    "rewrite_query",
]
