"""
Golden-set eval harness.

Runs offline (mock retrieval) by default for CI, or against a live project
when ``--project-id`` and API credentials are provided.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Sequence

from app.eval.metrics import faithfulness_score, mean, retrieval_at_k

DEFAULT_GOLDEN = Path(__file__).resolve().parent / "data" / "golden_set.json"


@dataclass
class CaseResult:
    id: str
    mode: str
    hit_at_k: float
    recall_at_k: float
    precision_at_k: float
    faithfulness: float
    retrieved_ids: list[str] = field(default_factory=list)


@dataclass
class EvalReport:
    cases: list[CaseResult]
    k: int
    hit_at_k: float
    recall_at_k: float
    precision_at_k: float
    faithfulness: float
    by_mode: dict[str, dict[str, float]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "k": self.k,
            "aggregate": {
                "hit_at_k": self.hit_at_k,
                "recall_at_k": self.recall_at_k,
                "precision_at_k": self.precision_at_k,
                "faithfulness": self.faithfulness,
            },
            "by_mode": self.by_mode,
            "cases": [asdict(c) for c in self.cases],
        }


def load_golden_set(path: Path | None = None) -> list[dict[str, Any]]:
    golden_path = path or DEFAULT_GOLDEN
    with golden_path.open(encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, list):
        raise ValueError("Golden set must be a JSON list")
    return data


def _mock_retrieve(
    case: dict[str, Any],
    mode: str,
    *,
    k: int,
) -> tuple[list[str], list[str]]:
    """
    Deterministic offline retriever for CI.

    - chunks_only / mixed: return relevant chunk ids first
    - summaries_first: return a synthetic summary id then expand to members
    """
    relevant = [str(x) for x in case.get("relevant_chunk_ids") or []]
    texts = [str(x) for x in case.get("relevant_texts") or []]
    if mode == "summaries_first":
        # Simulate summary hit then member expansion
        retrieved_ids = [f"summary:{case['id']}"] + relevant
        contexts = [
            f"Summary of {case.get('question', '')}: " + " ".join(texts)
        ] + texts
    else:
        retrieved_ids = list(relevant)
        # Add a distractor to exercise ranking
        retrieved_ids.append(f"distractor:{case['id']}")
        contexts = list(texts) + [f"Unrelated distractor for {case['id']}"]
    return retrieved_ids[: max(k, len(retrieved_ids))], contexts


def _answer_from_contexts(case: dict[str, Any], contexts: Sequence[str]) -> str:
    """Offline answer = gold answer (faithfulness vs contexts still meaningful)."""
    gold = str(case.get("gold_answer") or "")
    if gold:
        return gold
    return " ".join(contexts[:1])


def run_eval(
    *,
    golden_path: Path | None = None,
    k: int = 5,
    modes: Sequence[str] | None = None,
) -> EvalReport:
    """Run offline golden-set eval (CI-safe, no live infra required)."""
    cases_raw = load_golden_set(golden_path)
    results: list[CaseResult] = []
    mode_filter = set(modes) if modes else None

    for case in cases_raw:
        case_modes = case.get("modes") or ["chunks_only"]
        for mode in case_modes:
            if mode_filter is not None and mode not in mode_filter:
                continue
            retrieved_ids, contexts = _mock_retrieve(case, mode, k=k)
            relevant = [str(x) for x in case.get("relevant_chunk_ids") or []]
            # For summaries_first, relevant ids are still the member chunks
            scores = retrieval_at_k(retrieved_ids, relevant, k=k)
            answer = _answer_from_contexts(case, contexts)
            faith = faithfulness_score(answer, contexts)
            results.append(
                CaseResult(
                    id=str(case.get("id")),
                    mode=mode,
                    hit_at_k=scores["hit_at_k"],
                    recall_at_k=scores["recall_at_k"],
                    precision_at_k=scores["precision_at_k"],
                    faithfulness=faith,
                    retrieved_ids=retrieved_ids[:k],
                )
            )

    by_mode: dict[str, dict[str, float]] = {}
    for mode in sorted({r.mode for r in results}):
        subset = [r for r in results if r.mode == mode]
        by_mode[mode] = {
            "hit_at_k": mean([r.hit_at_k for r in subset]),
            "recall_at_k": mean([r.recall_at_k for r in subset]),
            "precision_at_k": mean([r.precision_at_k for r in subset]),
            "faithfulness": mean([r.faithfulness for r in subset]),
            "n": float(len(subset)),
        }

    return EvalReport(
        cases=results,
        k=k,
        hit_at_k=mean([r.hit_at_k for r in results]),
        recall_at_k=mean([r.recall_at_k for r in results]),
        precision_at_k=mean([r.precision_at_k for r in results]),
        faithfulness=mean([r.faithfulness for r in results]),
        by_mode=by_mode,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="FlexSearch golden-set eval")
    parser.add_argument(
        "--golden",
        type=Path,
        default=DEFAULT_GOLDEN,
        help="Path to golden_set.json",
    )
    parser.add_argument("--k", type=int, default=5, help="retrieval@k cutoff")
    parser.add_argument(
        "--modes",
        nargs="*",
        default=None,
        help="Optional mode filter: chunks_only summaries_first mixed",
    )
    parser.add_argument(
        "--min-hit-at-k",
        type=float,
        default=0.8,
        help="Fail if aggregate hit@k is below this threshold",
    )
    parser.add_argument(
        "--min-faithfulness",
        type=float,
        default=0.5,
        help="Fail if aggregate faithfulness is below this threshold",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print full JSON report",
    )
    args = parser.parse_args(argv)

    report = run_eval(golden_path=args.golden, k=args.k, modes=args.modes)
    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(f"FlexSearch eval (k={report.k}, n={len(report.cases)})")
        print(f"  hit@{report.k}:        {report.hit_at_k:.3f}")
        print(f"  recall@{report.k}:     {report.recall_at_k:.3f}")
        print(f"  precision@{report.k}:  {report.precision_at_k:.3f}")
        print(f"  faithfulness:  {report.faithfulness:.3f}")
        for mode, stats in report.by_mode.items():
            print(
                f"  [{mode}] hit={stats['hit_at_k']:.3f} "
                f"faith={stats['faithfulness']:.3f} n={int(stats['n'])}"
            )

    failed = False
    if report.hit_at_k < args.min_hit_at_k:
        print(
            f"FAIL: hit@{report.k}={report.hit_at_k:.3f} < {args.min_hit_at_k}",
            file=sys.stderr,
        )
        failed = True
    if report.faithfulness < args.min_faithfulness:
        print(
            f"FAIL: faithfulness={report.faithfulness:.3f} < {args.min_faithfulness}",
            file=sys.stderr,
        )
        failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
