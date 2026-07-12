"""Golden-set evaluation harness for FlexSearch RAG."""

from app.eval.harness import EvalReport, run_eval
from app.eval.metrics import faithfulness_score, retrieval_at_k

__all__ = [
    "EvalReport",
    "run_eval",
    "faithfulness_score",
    "retrieval_at_k",
]
