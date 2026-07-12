"""FlexSearch Backend - Chunking strategies (LangChain-backed)."""

from app.rag.chunking.base import BaseChunkingStrategy, Chunk
from app.rag.chunking.fixed_window import FixedWindowChunking
from app.rag.chunking.parent_child import ParentChildChunking
from app.rag.chunking.recursive import RecursiveChunking
from app.rag.chunking.semantic import SemanticChunking

__all__ = [
    "BaseChunkingStrategy",
    "Chunk",
    "FixedWindowChunking",
    "RecursiveChunking",
    "SemanticChunking",
    "ParentChildChunking",
]
