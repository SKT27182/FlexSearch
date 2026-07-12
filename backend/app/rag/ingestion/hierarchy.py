"""
Hierarchy extraction from markdown / heading-structured text.

Produces heading path metadata for chunks (section breadcrumbs).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)


@dataclass
class HeadingSpan:
    """A heading and the character range it owns until the next same-or-higher heading."""

    level: int
    title: str
    start: int
    end: int
    path: list[str] = field(default_factory=list)


def extract_heading_spans(text: str) -> list[HeadingSpan]:
    """Parse markdown ATX headings into spans covering the document."""
    matches = list(_HEADING_RE.finditer(text))
    if not matches:
        return []

    spans: list[HeadingSpan] = []
    stack: list[tuple[int, str]] = []

    for i, match in enumerate(matches):
        level = len(match.group(1))
        title = match.group(2).strip()
        start = match.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)

        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, title))
        path = [t for _, t in stack]
        spans.append(HeadingSpan(level=level, title=title, start=start, end=end, path=path))

    return spans


def heading_path_at(text: str, char_offset: int, spans: list[HeadingSpan] | None = None) -> list[str]:
    """Return the heading breadcrumb for a character offset."""
    spans = spans if spans is not None else extract_heading_spans(text)
    best: list[str] = []
    for span in spans:
        if span.start <= char_offset < span.end:
            best = span.path
        elif span.start > char_offset:
            break
    return best


def annotate_chunks_with_hierarchy(
    text: str,
    chunks: list[Any],
) -> None:
    """
    Mutate chunk.metadata with hierarchy fields:

    - heading_path: list[str]
    - section_title: str | None (leaf heading)
    """
    spans = extract_heading_spans(text)
    if not spans:
        return

    for chunk in chunks:
        path = heading_path_at(text, getattr(chunk, "start_char", 0), spans)
        if not path:
            continue
        # Copy before mutate — shared metadata dicts must not collapse paths
        meta = dict(getattr(chunk, "metadata", None) or {})
        meta["heading_path"] = path
        meta["section_title"] = path[-1]
        chunk.metadata = meta
