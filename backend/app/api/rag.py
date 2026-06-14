"""RAG configuration metadata for UI forms."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query

from app.core.dependencies import get_current_active_user
from app.db.models import RagMode, User
from app.schemas.rag_config import GraphRagConfig, VectorRagConfig

router = APIRouter(prefix="/rag", tags=["rag"])


@router.get("/options")
async def get_rag_options(
    _: Annotated[User, Depends(get_current_active_user)],
    mode: Literal["vector", "graph"] = Query(default="vector"),
) -> dict:
    if mode == "graph":
        defaults = GraphRagConfig.from_settings()
        return {
            "mode": "graph",
            "defaults": defaults.model_dump(mode="json"),
            "extraction_strategies": ["ocr", "vlm"],
            "retrieval_strategies": ["graph_local", "graph_global"],
            "indexing_params": {
                "max_entities_per_passage": 20,
                "embed_entities": True,
            },
            "extraction_params": {
                "passage_chunk_size": 800,
            },
            "retrieval_params": {
                "graph_local": {"max_hops": 2, "top_entities": 10},
                "graph_global": {"top_passages": 5},
            },
        }

    defaults = VectorRagConfig.from_settings()
    return {
        "mode": "vector",
        "defaults": defaults.model_dump(mode="json"),
        "extraction_strategies": ["ocr", "vlm"],
        "chunking_strategies": [
            "fixed_window",
            "recursive",
            "semantic",
            "parent_child",
        ],
        "retrieval_strategies": ["dense", "bm25", "hybrid", "parent_child"],
        "reranking_strategies": ["none", "cross_encoder"],
        "chunking_params": {
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
        },
        "retrieval_params": {
            "dense": {"score_threshold": None},
            "bm25": {"k1": 1.5, "b": 0.75},
            "hybrid": {"rrf_k": 60},
            "parent_child": {},
        },
    }
