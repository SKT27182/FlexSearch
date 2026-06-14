"""RAG configuration metadata for UI forms."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query

from app.core.dependencies import get_current_active_user
from app.db.models import RagMode, User
from app.schemas.rag_config import RagConfig

router = APIRouter(prefix="/rag", tags=["rag"])

VECTOR_RETRIEVAL = ["dense", "bm25", "hybrid", "parent_child"]
GRAPH_RETRIEVAL = ["graph_local", "graph_global"]


@router.get("/options")
async def get_rag_options(
    _: Annotated[User, Depends(get_current_active_user)],
    rag_mode: Literal["vector", "graph"] | None = Query(default=None),
) -> dict:
    mode = rag_mode or "vector"
    if mode == "graph":
        defaults = RagConfig.for_mode(RagMode.GRAPH)
        retrieval_strategies = GRAPH_RETRIEVAL
        retrieval_params = {
            "graph_local": {"community_level": 2, "max_context_tokens": 12000},
            "graph_global": {
                "community_level": 2,
                "dynamic_community_selection": False,
                "max_context_tokens": 12000,
            },
        }
        chunking_strategies: list[str] = []
        chunking_params: dict = {}
    else:
        defaults = RagConfig.from_settings()
        retrieval_strategies = VECTOR_RETRIEVAL
        retrieval_params = {
            "dense": {"score_threshold": None},
            "bm25": {"k1": 1.5, "b": 0.75},
            "hybrid": {"rrf_k": 60},
            "parent_child": {},
        }
        chunking_strategies = [
            "fixed_window",
            "recursive",
            "semantic",
            "parent_child",
        ]
        chunking_params = {
            "fixed_window": {"chunk_size": 512, "overlap": 50},
            "recursive": {"chunk_size": 512, "overlap": 50},
            "semantic": {
                "similarity_threshold": 0.5,
                "min_chunk_size": 100,
                "max_chunk_size": 1000,
            },
            "parent_child": {
                "parent_chunk_size": 1500,
                "child_chunk_size": 300,
                "overlap": 50,
            },
        }

    return {
        "rag_mode": mode,
        "defaults": defaults.model_dump(mode="json"),
        "extraction_strategies": ["ocr", "vlm"],
        "chunking_strategies": chunking_strategies,
        "retrieval_strategies": retrieval_strategies,
        "reranking_strategies": ["none", "cross_encoder"],
        "graph_indexing": {
            "enabled": True,
            "method": ["standard", "nlp"],
            "community_level": {"min": 0, "max": 4, "default": 2},
        },
        "chunking_params": chunking_params,
        "retrieval_params": retrieval_params,
    }
