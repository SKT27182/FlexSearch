"""Suggestion service exports."""

from app.services.suggestion.service import (
    generate_followup_questions,
    generate_project_suggestions,
)

__all__ = ["generate_followup_questions", "generate_project_suggestions"]
