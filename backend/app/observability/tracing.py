"""Lightweight stage timing that records into the metrics registry."""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Generator

from app.observability.metrics import metrics


@contextmanager
def timed_stage(stage: str, **labels: str) -> Generator[None, None, None]:
    """Time a code block and record ``flexsearch_stage_latency_seconds``."""
    start = time.perf_counter()
    try:
        yield
    finally:
        metrics.observe_stage(stage, time.perf_counter() - start, **labels)
