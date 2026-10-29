from __future__ import annotations

import os
import uuid
from collections.abc import Iterator

import psycopg
import pytest
from psycopg import sql

from autoseguro.config import Settings


@pytest.fixture
def fresh_database() -> Iterator[Settings]:
    """Cria um banco vazio e descartável no Postgres de DATABASE_URL e devolve Settings
    apontando para ele.

    Sem Postgres acessível, o teste é pulado; com REQUIRE_DB=1 (CI), falha.
    """
    base = Settings(_env_file=None)
    name = f"autoseguro_test_{uuid.uuid4().hex[:8]}"
    try:
        admin = psycopg.connect(base.database_url, autocommit=True, connect_timeout=3)
    except psycopg.OperationalError as exc:
        if os.getenv("REQUIRE_DB") == "1":
            raise
        pytest.skip(f"Postgres indisponível em DATABASE_URL: {exc}")

    with admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        prefix, _, _ = base.database_url.rpartition("/")
        try:
            yield Settings(database_url=f"{prefix}/{name}", _env_file=None)
        finally:
            admin.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
            )
