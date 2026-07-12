"""Pipeline debug timing helpers for SSE debug events + metrics."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class DebugEvent:
    """One stage timing / detail record for SSE ``debug`` events."""

    stage: str
    duration_ms: int
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "duration_ms": self.duration_ms,
            "detail": self.detail,
        }


class StageTimer:
    """Collect per-stage timings.

    Timings are always recorded (for metrics). When ``enabled`` is True they
    are also exposed via SSE ``debug`` events / ChatAnswer.debug.
    """

    def __init__(self, *, enabled: bool = False) -> None:
        self.enabled = enabled
        self.events: list[DebugEvent] = []
        self._marks: dict[str, float] = {}

    def start(self, stage: str) -> None:
        self._marks[stage] = time.perf_counter()

    def end(self, stage: str, **detail: Any) -> DebugEvent | None:
        started = self._marks.pop(stage, None)
        duration_ms = (
            int((time.perf_counter() - started) * 1000) if started is not None else 0
        )
        event = DebugEvent(stage=stage, duration_ms=duration_ms, detail=detail)
        self.events.append(event)
        return event if self.enabled else None

    def summary(self) -> dict[str, Any]:
        return {
            "stages": [e.to_dict() for e in self.events],
            "total_stage_ms": sum(e.duration_ms for e in self.events),
        }
