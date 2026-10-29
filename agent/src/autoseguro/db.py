"""Acesso ao Postgres: migrações (Alembic) e checagem de conexão."""

from __future__ import annotations

import psycopg
from alembic import command
from alembic.config import Config

from autoseguro.config import Settings

APP_SCHEMA = "agent"
CHECKPOINT_SCHEMA = "langgraph"


def alembic_config(settings: Settings) -> Config:
    """Config do Alembic montada em código: não depende de um `alembic.ini` no diretório
    atual, então funciona igual no host e dentro do container."""
    cfg = Config()
    cfg.set_main_option("script_location", "autoseguro:migrations")
    cfg.set_main_option("sqlalchemy.url", settings.sqlalchemy_url.replace("%", "%%"))
    return cfg


def migrate(settings: Settings, revision: str = "head") -> None:
    command.upgrade(alembic_config(settings), revision)


def downgrade(settings: Settings, revision: str = "base") -> None:
    command.downgrade(alembic_config(settings), revision)


def ping(settings: Settings) -> str:
    """Devolve a versão do servidor; lança `psycopg.OperationalError` se não conectar."""
    with psycopg.connect(settings.database_url, connect_timeout=3) as conn:
        row = conn.execute("SHOW server_version").fetchone()
        return str(row[0]) if row else "?"
