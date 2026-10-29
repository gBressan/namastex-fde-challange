from __future__ import annotations

import asyncio
import os
import sys
import uuid
from collections.abc import Callable, Iterator, Mapping

import httpx
import psycopg
import pytest
from psycopg import sql

from autoseguro.config import Settings


def pytest_asyncio_loop_factories(
    config: pytest.Config, item: pytest.Item
) -> Mapping[str, Callable[[], asyncio.AbstractEventLoop]] | None:
    # O psycopg assíncrono não funciona com o ProactorEventLoop, padrão do Windows.
    if sys.platform == "win32":
        return {"selector": asyncio.SelectorEventLoop}
    return None


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


@pytest.fixture
def quote_api_url() -> str:
    """URL da quote-api rodando (docker compose). Sem ela, o teste é pulado; com
    REQUIRE_API=1 (CI), falha."""
    url = Settings(_env_file=None).quote_api_url
    try:
        httpx.get(f"{url}/health", timeout=3).raise_for_status()
    except httpx.HTTPError as exc:
        if os.getenv("REQUIRE_API") == "1":
            raise
        pytest.skip(f"quote-api indisponível em {url}: {exc}")
    return url
