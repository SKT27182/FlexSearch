"""
Post-extract text preprocessing for ingest quality.

- ftfy Unicode repair
- Whitespace normalization
- Repeated header/footer line heuristics
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter

from app.utils.logger import create_logger

logger = create_logger(__name__)

try:
    import ftfy as _ftfy
except ImportError:  # pragma: no cover - optional until deps sync
    _ftfy = None

_MULTI_BLANK = re.compile(r"\n{3,}")
_TRAILING_WS = re.compile(r"[ \t]+\n")
_PAGE_NUM = re.compile(r"^\s*(?:page\s+)?\d+(?:\s*/\s*\d+)?\s*$", re.IGNORECASE)


def normalize_whitespace(text: str) -> str:
    """Collapse excessive blank lines and strip trailing spaces per line."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _TRAILING_WS.sub("\n", text)
    text = _MULTI_BLANK.sub("\n\n", text)
    return text.strip()


def remove_repeated_headers_footers(
    text: str,
    *,
    min_occurrences: int = 3,
    max_line_len: int = 120,
) -> str:
    """
    Drop short lines that repeat across many pages/sections (headers/footers).

    Splits on form-feed or double-newline page-ish blocks when possible;
    otherwise uses the whole document's line frequency.
    """
    if not text.strip():
        return text

    blocks = re.split(r"\f|\n{2,}", text)
    if len(blocks) < min_occurrences:
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        counts = Counter(lines)
        drop = {
            ln
            for ln, n in counts.items()
            if n >= min_occurrences
            and len(ln) <= max_line_len
            and (_PAGE_NUM.match(ln) or n >= max(min_occurrences, len(lines) // 10))
        }
        if not drop:
            return text
        return "\n".join(ln for ln in text.splitlines() if ln.strip() not in drop)

    edge_lines: list[str] = []
    for block in blocks:
        blines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        if not blines:
            continue
        edge_lines.append(blines[0])
        if len(blines) > 1:
            edge_lines.append(blines[-1])

    counts = Counter(edge_lines)
    drop = {
        ln
        for ln, n in counts.items()
        if n >= min_occurrences and len(ln) <= max_line_len
    }
    if not drop:
        return text

    out_lines: list[str] = []
    for ln in text.splitlines():
        stripped = ln.strip()
        if stripped and stripped in drop:
            continue
        out_lines.append(ln)
    return "\n".join(out_lines)


def preprocess_extracted_text(
    text: str,
    *,
    fix_encoding: bool = True,
    normalize_ws: bool = True,
    strip_headers_footers: bool = True,
) -> str:
    """Apply the full post-extract preprocess pipeline."""
    if not text:
        return text

    if fix_encoding:
        if _ftfy is not None:
            text = _ftfy.fix_text(text)
        else:
            text = unicodedata.normalize("NFKC", text)

    if strip_headers_footers:
        before = len(text)
        text = remove_repeated_headers_footers(text)
        if len(text) < before:
            logger.debug(
                "Header/footer heuristic removed %d chars",
                before - len(text),
            )

    if normalize_ws:
        text = normalize_whitespace(text)

    return text
