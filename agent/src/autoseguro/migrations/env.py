"""Ambiente do Alembic. A URL vem da Config montada em `autoseguro.db.alembic_config`."""

from __future__ import annotations

from alembic import context
from sqlalchemy import create_engine, pool, text

from autoseguro.db import APP_SCHEMA

config = context.config


def run_migrations_online() -> None:
    url = config.get_main_option("sqlalchemy.url")
    if not url:
        raise RuntimeError("sqlalchemy.url não configurada")
    engine = create_engine(url, poolclass=pool.NullPool)
    with engine.connect() as connection:
        # A tabela de versão do Alembic fica no schema da aplicação, que precisa existir antes.
        connection.execute(text(f"CREATE SCHEMA IF NOT EXISTS {APP_SCHEMA}"))
        connection.commit()
        context.configure(
            connection=connection,
            version_table_schema=APP_SCHEMA,
            include_schemas=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    raise RuntimeError("Modo offline não é suportado; rode `autoseguro db migrate`.")
run_migrations_online()
