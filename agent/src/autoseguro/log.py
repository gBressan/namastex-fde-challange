"""Logs estruturados com structlog.

Todo log carrega o contexto vinculado com `bind_contextvars` (ex.: `conversation_id`),
o que permite filtrar uma conversa inteira nos logs. Antes de renderizar, todo valor passa
pelo mascaramento de dados pessoais.
"""

from __future__ import annotations

import logging
import sys
from typing import Literal

import structlog

from autoseguro.safety.pii import structlog_redactor


def configure_logging(level: str = "INFO", fmt: Literal["json", "console"] = "json") -> None:
    renderer: structlog.typing.Processor = (
        structlog.processors.JSONRenderer() if fmt == "json" else structlog.dev.ConsoleRenderer()
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            # Por último antes de renderizar: pega também o texto das exceções formatadas.
            structlog_redactor,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level.upper())),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.typing.FilteringBoundLogger:
    logger: structlog.typing.FilteringBoundLogger = structlog.get_logger(name)
    return logger
