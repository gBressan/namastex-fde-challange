from __future__ import annotations

import psycopg
import pytest

from autoseguro import db
from autoseguro.config import Settings

pytestmark = pytest.mark.db

EXPECTED_TABLES = {
    "alembic_version",
    "conversations",
    "messages",
    "events",
    "quotes",
    "quote_attempts",
    "handoffs",
    "pending_quotes",
}


def _tables(settings: Settings, schema: str) -> set[str]:
    with psycopg.connect(settings.database_url) as conn:
        rows = conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = %s",
            (schema,),
        ).fetchall()
    return {row[0] for row in rows}


def test_migracao_cria_schemas_e_tabelas(fresh_database: Settings) -> None:
    db.migrate(fresh_database)

    assert _tables(fresh_database, "agent") == EXPECTED_TABLES
    with psycopg.connect(fresh_database.database_url) as conn:
        schemas = {
            r[0] for r in conn.execute("SELECT schema_name FROM information_schema.schemata")
        }
    assert {"agent", "langgraph"} <= schemas


def test_migracao_e_idempotente(fresh_database: Settings) -> None:
    db.migrate(fresh_database)
    db.migrate(fresh_database)
    assert _tables(fresh_database, "agent") == EXPECTED_TABLES


def test_downgrade_remove_as_tabelas(fresh_database: Settings) -> None:
    db.migrate(fresh_database)
    db.downgrade(fresh_database)
    assert _tables(fresh_database, "agent") == {"alembic_version"}


def test_events_e_append_only(fresh_database: Settings) -> None:
    db.migrate(fresh_database)
    with psycopg.connect(fresh_database.database_url, autocommit=True) as conn:
        conn.execute("INSERT INTO agent.events (type, payload) VALUES ('teste', '{}')")
        with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
            conn.execute("UPDATE agent.events SET type = 'alterado'")
        with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
            conn.execute("DELETE FROM agent.events")


def test_mensagem_duplicada_e_rejeitada(fresh_database: Settings) -> None:
    db.migrate(fresh_database)
    with psycopg.connect(fresh_database.database_url, autocommit=True) as conn:
        row = conn.execute(
            "INSERT INTO agent.conversations (channel, status) VALUES ('cli', 'saudacao') "
            "RETURNING id"
        ).fetchone()
        assert row is not None
        insert = (
            "INSERT INTO agent.messages "
            "(conversation_id, direction, external_message_id, type, body_redacted) "
            "VALUES (%s, 'in', 'wamid.1', 'text', 'oi')"
        )
        conn.execute(insert, (row[0],))
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute(insert, (row[0],))
