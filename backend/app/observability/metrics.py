"""
In-process Prometheus-text metrics for FlexSearch.

No external Prometheus client required — scrape GET /metrics.
Counters and histograms are process-local (per API / worker process).
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Iterable


def _labels_key(labels: dict[str, str] | None) -> tuple[tuple[str, str], ...]:
    if not labels:
        return ()
    return tuple(sorted((str(k), str(v)) for k, v in labels.items()))


def _format_labels(labels: tuple[tuple[str, str], ...]) -> str:
    if not labels:
        return ""
    inner = ",".join(f'{k}="{v}"' for k, v in labels)
    return "{" + inner + "}"


@dataclass
class _Counter:
    name: str
    help: str
    values: dict[tuple[tuple[str, str], ...], float] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def inc(self, amount: float = 1.0, **labels: str) -> None:
        key = _labels_key(labels)
        with self.lock:
            self.values[key] = self.values.get(key, 0.0) + amount

    def render(self) -> list[str]:
        lines = [
            f"# HELP {self.name} {self.help}",
            f"# TYPE {self.name} counter",
        ]
        with self.lock:
            items = list(self.values.items())
        for key, value in items:
            lines.append(f"{self.name}{_format_labels(key)} {value}")
        if not items:
            lines.append(f"{self.name} 0")
        return lines


@dataclass
class _Histogram:
    """Simple cumulative histogram with fixed buckets (seconds or ms as configured)."""

    name: str
    help: str
    buckets: tuple[float, ...]
    counts: dict[tuple[tuple[str, str], ...], list[float]] = field(default_factory=dict)
    sums: dict[tuple[tuple[str, str], ...], float] = field(default_factory=dict)
    totals: dict[tuple[tuple[str, str], ...], float] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def observe(self, value: float, **labels: str) -> None:
        key = _labels_key(labels)
        with self.lock:
            if key not in self.counts:
                self.counts[key] = [0.0] * len(self.buckets)
                self.sums[key] = 0.0
                self.totals[key] = 0.0
            for i, bound in enumerate(self.buckets):
                if value <= bound:
                    self.counts[key][i] += 1.0
            self.sums[key] += value
            self.totals[key] += 1.0

    def render(self) -> list[str]:
        lines = [
            f"# HELP {self.name} {self.help}",
            f"# TYPE {self.name} histogram",
        ]
        with self.lock:
            keys = list(self.counts.keys())
        if not keys:
            # Empty series so scrapers still see the metric family
            for bound in self.buckets:
                le = "+Inf" if bound == float("inf") else str(bound)
                lines.append(f'{self.name}_bucket{{le="{le}"}} 0')
            lines.append(f"{self.name}_sum 0")
            lines.append(f"{self.name}_count 0")
            return lines

        for key in keys:
            with self.lock:
                # observe() stores cumulative bucket counts (value <= bound).
                counts = list(self.counts[key])
                total_sum = self.sums[key]
                total_count = self.totals[key]
            label_suffix = _format_labels(key)
            for bound, count in zip(self.buckets, counts):
                le = "+Inf" if bound == float("inf") else str(bound)
                if label_suffix:
                    # {a="b"} → {a="b",le="..."}
                    inner = label_suffix[1:-1]
                    lines.append(f'{self.name}_bucket{{{inner},le="{le}"}} {count}')
                else:
                    lines.append(f'{self.name}_bucket{{le="{le}"}} {count}')
            lines.append(f"{self.name}_sum{label_suffix} {total_sum}")
            lines.append(f"{self.name}_count{label_suffix} {total_count}")
        return lines


# Default latency buckets (seconds)
_LATENCY_BUCKETS = (
    0.005,
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
    30.0,
    float("inf"),
)


class MetricsRegistry:
    """Process-local metrics used by chat, retrieval, ingest, and LLM paths."""

    def __init__(self) -> None:
        self.chat_requests = _Counter(
            "flexsearch_chat_requests_total",
            "Chat answer/stream requests",
        )
        self.chat_empty_retrieval = _Counter(
            "flexsearch_chat_empty_retrieval_total",
            "Chat turns with empty retrieval",
        )
        self.stage_latency = _Histogram(
            "flexsearch_stage_latency_seconds",
            "Latency of chat/query/ingest stages",
            _LATENCY_BUCKETS,
        )
        self.retrieval_requests = _Counter(
            "flexsearch_retrieval_requests_total",
            "RAGPipeline.retrieve calls",
        )
        self.retrieval_empty = _Counter(
            "flexsearch_retrieval_empty_total",
            "Retrieval calls that returned zero hits",
        )
        self.llm_requests = _Counter(
            "flexsearch_llm_requests_total",
            "LLM complete/stream calls",
        )
        self.llm_tokens = _Counter(
            "flexsearch_llm_tokens_total",
            "LLM tokens by direction (input|output)",
        )
        self.ingest_documents = _Counter(
            "flexsearch_ingest_documents_total",
            "Document ingest completions by status",
        )
        self.rate_limit_hits = _Counter(
            "flexsearch_rate_limit_hits_total",
            "API rate-limit rejections",
        )
        self.rag_safety_events = _Counter(
            "flexsearch_rag_safety_events_total",
            "RAG grounding, citation, and prompt-injection signals",
        )
        self._started_at = time.time()

    def observe_stage(self, stage: str, seconds: float, **extra: str) -> None:
        labels = {"stage": stage, **extra}
        self.stage_latency.observe(seconds, **labels)

    def record_chat(
        self,
        *,
        path: str,
        empty_retrieval: bool,
        latency_ms: int,
        input_tokens: int = 0,
        output_tokens: int = 0,
        rag_mode: str = "vector",
    ) -> None:
        self.chat_requests.inc(path=path, rag_mode=rag_mode)
        if empty_retrieval:
            self.chat_empty_retrieval.inc(path=path, rag_mode=rag_mode)
        self.observe_stage(
            "chat_total", latency_ms / 1000.0, path=path, rag_mode=rag_mode
        )
        if input_tokens:
            self.llm_tokens.inc(input_tokens, direction="input", source="chat")
        if output_tokens:
            self.llm_tokens.inc(output_tokens, direction="output", source="chat")

    def record_retrieval(
        self,
        *,
        strategy: str,
        hit_count: int,
        seconds: float,
        rag_mode: str = "vector",
    ) -> None:
        self.retrieval_requests.inc(strategy=strategy, rag_mode=rag_mode)
        if hit_count == 0:
            self.retrieval_empty.inc(strategy=strategy, rag_mode=rag_mode)
        self.observe_stage(
            "retrieve",
            seconds,
            strategy=strategy,
            rag_mode=rag_mode,
        )

    def record_llm(
        self,
        *,
        kind: str,
        input_tokens: int,
        output_tokens: int,
        seconds: float,
    ) -> None:
        self.llm_requests.inc(kind=kind)
        if input_tokens:
            self.llm_tokens.inc(input_tokens, direction="input", source=kind)
        if output_tokens:
            self.llm_tokens.inc(output_tokens, direction="output", source=kind)
        self.observe_stage("llm", seconds, kind=kind)

    def record_ingest(self, *, status: str, seconds: float | None = None) -> None:
        self.ingest_documents.inc(status=status)
        if seconds is not None:
            self.observe_stage("ingest", seconds, status=status)

    def record_rag_safety(self, event: str, amount: int = 1) -> None:
        if amount > 0:
            self.rag_safety_events.inc(amount, event=event)

    def empty_retrieval_rate(self) -> float:
        """Approximate empty-retrieval rate across chat paths (0–1)."""
        with self.chat_requests.lock:
            total = sum(self.chat_requests.values.values())
        with self.chat_empty_retrieval.lock:
            empty = sum(self.chat_empty_retrieval.values.values())
        if total <= 0:
            return 0.0
        return empty / total

    def snapshot(self) -> dict:
        """JSON-friendly snapshot for protected operations dashboards."""
        with self.chat_requests.lock:
            chat_total = sum(self.chat_requests.values.values())
        with self.chat_empty_retrieval.lock:
            empty = sum(self.chat_empty_retrieval.values.values())
        with self.llm_tokens.lock:
            tokens = dict(self.llm_tokens.values)
        return {
            "uptime_seconds": round(time.time() - self._started_at, 1),
            "chat_requests_total": chat_total,
            "chat_empty_retrieval_total": empty,
            "empty_retrieval_rate": round(self.empty_retrieval_rate(), 4),
            "llm_tokens": {
                ",".join(f"{k}={v}" for k, v in key) if key else "all": val
                for key, val in tokens.items()
            },
        }

    def render_prometheus(self) -> str:
        series: Iterable[_Counter | _Histogram] = (
            self.chat_requests,
            self.chat_empty_retrieval,
            self.stage_latency,
            self.retrieval_requests,
            self.retrieval_empty,
            self.llm_requests,
            self.llm_tokens,
            self.ingest_documents,
            self.rate_limit_hits,
            self.rag_safety_events,
        )
        lines: list[str] = []
        for metric in series:
            lines.extend(metric.render())
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"


metrics = MetricsRegistry()
