"""Jinja2 prompt pack loader for chat / RAG answer generation."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

PROMPTS_DIR = Path(__file__).resolve().parent


@lru_cache(maxsize=1)
def _env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(PROMPTS_DIR)),
        autoescape=select_autoescape(enabled_extensions=()),
        trim_blocks=True,
        lstrip_blocks=True,
    )


def render_prompt(name: str, **context: Any) -> str:
    """Render a prompt template by stem name (e.g. ``system``, ``answer``, ``clarify``)."""
    template_name = name if name.endswith(".j2") else f"{name}.j2"
    return _env().get_template(template_name).render(**context).strip()


def list_prompt_names() -> list[str]:
    return sorted(p.stem for p in PROMPTS_DIR.glob("*.j2"))
