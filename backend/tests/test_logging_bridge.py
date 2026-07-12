"""Tests for unified / colored third-party logging."""

from __future__ import annotations

import logging

from app.core.config import settings
from app.utils.logger import CustomFormatter
from app.utils.logging_bridge import (
    configure_third_party_loggers,
    sql_echo_enabled,
)


def _console_handlers(logger: logging.Logger) -> list[logging.Handler]:
    return [
        h
        for h in logger.handlers
        if isinstance(h, logging.StreamHandler)
        and not isinstance(h, logging.FileHandler)
    ]


def test_configure_third_party_uses_colored_formatter(monkeypatch) -> None:
    monkeypatch.setattr(settings, "sql_echo", False)
    monkeypatch.setattr(settings, "log_level", "INFO")

    # Simulate SQLAlchemy echo= / uvicorn default plain handlers.
    for name in ("sqlalchemy.engine", "uvicorn", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers.clear()
        plain = logging.StreamHandler()
        plain.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
        )
        lg.addHandler(plain)

    configure_third_party_loggers("INFO")

    engine = logging.getLogger("sqlalchemy.engine")
    assert engine.level == logging.WARNING  # quiet unless sql_echo / DEBUG
    consoles = _console_handlers(engine)
    assert len(consoles) == 1
    assert isinstance(consoles[0].formatter, CustomFormatter)

    access = logging.getLogger("uvicorn.access")
    consoles = _console_handlers(access)
    assert len(consoles) == 1
    assert isinstance(consoles[0].formatter, CustomFormatter)
    assert access.propagate is False

    error = logging.getLogger("uvicorn.error")
    assert _console_handlers(error) == []
    assert error.propagate is True


def test_sql_echo_enables_engine_info(monkeypatch) -> None:
    monkeypatch.setattr(settings, "sql_echo", True)
    monkeypatch.setattr(settings, "log_level", "INFO")
    assert sql_echo_enabled() is True
    configure_third_party_loggers("INFO")
    assert logging.getLogger("sqlalchemy.engine").level == logging.INFO


def test_log_level_debug_enables_sql_echo(monkeypatch) -> None:
    monkeypatch.setattr(settings, "sql_echo", False)
    monkeypatch.setattr(settings, "log_level", "DEBUG")
    assert sql_echo_enabled() is True


def test_postgres_engine_echo_disabled() -> None:
    from app.db.postgres import engine

    assert engine.echo is False
