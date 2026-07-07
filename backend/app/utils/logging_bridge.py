"""Bridge third-party loggers (GraphRAG, LiteLLM, etc.) into the FlexSearch backend log.

Dev runs tee stdout to ``~/.local/share/dev-logs/flexsearch/backend.log`` via the
Makefile, but GraphRAG's ``init_loggers`` writes only to a workspace-local file
(``indexing-engine.log``), so extraction errors never appeared in the backend log.
This module attaches a shared file handler on the root logger and routes GraphRAG
loggers through it instead.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from app.core.config import settings

_CONFIGURED = False

# Loggers that should not keep isolated file handlers from GraphRAG init.
_BRIDGED_LOGGER_NAMES = (
    "graphrag",
    "graphrag_llm",
    "litellm",
    "graphrag_common",
)

_LOG_FORMAT = (
    "%(asctime)s - %(process)d - %(name)s - %(levelname)s - "
    "%(funcName)s - %(lineno)d - %(message)s"
)
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def backend_log_path() -> Path:
    """Resolved backend log file used by ``make dev-local`` (append mode)."""
    explicit = os.environ.get("BACKEND_LOG_FILE", "").strip()
    if explicit:
        return Path(explicit).expanduser()
    return (
        Path.home() / ".local" / "share" / "dev-logs" / "flexsearch" / "backend.log"
    )


def _level_int(level: str | int) -> int:
    if isinstance(level, int):
        return level
    mapping = {
        "notset": logging.NOTSET,
        "debug": logging.DEBUG,
        "info": logging.INFO,
        "verbose": logging.INFO + 3,
        "warning": logging.WARNING,
        "error": logging.ERROR,
        "critical": logging.CRITICAL,
    }
    return mapping.get(level.lower(), logging.INFO)


def _has_backend_file_handler(root: logging.Logger, path: Path) -> bool:
    target = str(path.resolve())
    for handler in root.handlers:
        if isinstance(handler, logging.FileHandler):
            if Path(handler.baseFilename).resolve() == Path(target):
                return True
    return False


def setup_unified_logging(level: str | None = None) -> Path:
    """Attach a shared backend log file handler on the root logger (idempotent)."""
    global _CONFIGURED
    log_path = backend_log_path()
    level_int = _level_int(level or settings.log_level)

    root = logging.getLogger()
    root.setLevel(min(root.level or level_int, level_int))

    if not _has_backend_file_handler(root, log_path):
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_path, mode="a", encoding="utf-8")
        file_handler.setLevel(level_int)
        file_handler.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT))
        root.addHandler(file_handler)

    for name in _BRIDGED_LOGGER_NAMES:
        bridged = logging.getLogger(name)
        bridged.handlers.clear()
        bridged.propagate = True
        bridged.setLevel(level_int)

    _CONFIGURED = True
    return log_path


def configure_graphrag_loggers(*, verbose: bool = False) -> None:
    """Route GraphRAG loggers to the unified backend log instead of workspace files."""
    setup_unified_logging()
    level_int = logging.DEBUG if verbose else _level_int(settings.log_level)
    for name in ("graphrag", "graphrag_llm"):
        lg = logging.getLogger(name)
        lg.handlers.clear()
        lg.propagate = True
        lg.setLevel(level_int)


def patch_graphrag_init_loggers() -> None:
    """Replace GraphRAG ``init_loggers`` so builds log to the backend file."""
    import graphrag.logger.standard_logging as standard_logging

    if getattr(standard_logging, "_flexsearch_patched", False):
        return

    def flexsearch_init_loggers(
        config,
        verbose: bool = False,
        filename: str = standard_logging.DEFAULT_LOG_FILENAME,
    ) -> None:
        del config, filename
        configure_graphrag_loggers(verbose=verbose)

    standard_logging.init_loggers = flexsearch_init_loggers
    standard_logging._flexsearch_patched = True
