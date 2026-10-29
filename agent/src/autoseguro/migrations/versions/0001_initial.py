"""Schema inicial: conversas, mensagens, eventos, cotações, handoffs e fila de cotação pendente.

Revision ID: 0001
Revises:
Create Date: 2026-10-29
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "agent"


def _id() -> sa.Column[Any]:
    return sa.Column(
        "id", UUID(as_uuid=False), primary_key=True, server_default=sa.text("gen_random_uuid()")
    )


def _created_at() -> sa.Column[Any]:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )


def _conversation_fk() -> sa.Column[Any]:
    return sa.Column(
        "conversation_id",
        UUID(as_uuid=False),
        sa.ForeignKey(f"{SCHEMA}.conversations.id"),
        nullable=False,
    )


def upgrade() -> None:
    # Schema do checkpointer do LangGraph (as tabelas são criadas pelo AsyncPostgresSaver.setup()).
    op.execute("CREATE SCHEMA IF NOT EXISTS langgraph")

    op.create_table(
        "conversations",
        _id(),
        sa.Column("channel", sa.Text, nullable=False),
        # Hash (com sal) do identificador externo, ex.: telefone. Nunca o valor original.
        sa.Column("external_ref_hash", sa.Text, nullable=True),
        sa.Column("status", sa.Text, nullable=False),
        _created_at(),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_conversations_external_ref_hash", "conversations", ["external_ref_hash"], schema=SCHEMA
    )

    op.create_table(
        "messages",
        _id(),
        _conversation_fk(),
        sa.Column("direction", sa.Text, nullable=False),
        sa.Column("external_message_id", sa.Text, nullable=True),
        sa.Column("type", sa.Text, nullable=False),
        # Texto já mascarado: o original nunca é gravado.
        sa.Column("body_redacted", sa.Text, nullable=False),
        _created_at(),
        sa.CheckConstraint("direction IN ('in', 'out')", name="ck_messages_direction"),
        sa.CheckConstraint(
            "type IN ('text', 'image', 'audio', 'document')", name="ck_messages_type"
        ),
        sa.UniqueConstraint(
            "conversation_id", "external_message_id", name="uq_messages_external_id"
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_messages_conversation", "messages", ["conversation_id", "created_at"], schema=SCHEMA
    )

    op.create_table(
        "events",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("conversation_id", UUID(as_uuid=False), nullable=True),
        sa.Column("type", sa.Text, nullable=False),
        sa.Column("payload", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        _created_at(),
        schema=SCHEMA,
    )
    op.create_index("ix_events_conversation", "events", ["conversation_id", "id"], schema=SCHEMA)
    op.create_index("ix_events_type", "events", ["type", "created_at"], schema=SCHEMA)
    # Append-only: eventos não são alterados nem apagados pela aplicação.
    op.execute(
        f"""
        CREATE FUNCTION {SCHEMA}.events_append_only() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'agent.events é append-only';
        END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER events_append_only BEFORE UPDATE OR DELETE ON {SCHEMA}.events
            FOR EACH ROW EXECUTE FUNCTION {SCHEMA}.events_append_only();
        """
    )

    op.create_table(
        "quotes",
        _id(),
        sa.Column("conversation_id", UUID(as_uuid=False), nullable=True),
        sa.Column("request", JSONB, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("result", JSONB, nullable=True),
        sa.Column("cache_key", sa.Text, nullable=False),
        sa.Column("from_cache", sa.Boolean, nullable=False, server_default=sa.false()),
        _created_at(),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'succeeded', 'refused', 'invalid', 'unavailable')",
            name="ck_quotes_status",
        ),
        schema=SCHEMA,
    )
    op.create_index("ix_quotes_conversation", "quotes", ["conversation_id"], schema=SCHEMA)

    op.create_table(
        "quote_attempts",
        _id(),
        sa.Column(
            "quote_id", UUID(as_uuid=False), sa.ForeignKey(f"{SCHEMA}.quotes.id"), nullable=False
        ),
        sa.Column("attempt_no", sa.SmallInteger, nullable=False),
        sa.Column("status_code", sa.SmallInteger, nullable=True),
        sa.Column("error_kind", sa.Text, nullable=True),
        sa.Column("latency_ms", sa.Integer, nullable=False),
        _created_at(),
        sa.UniqueConstraint("quote_id", "attempt_no", name="uq_quote_attempts_no"),
        schema=SCHEMA,
    )

    op.create_table(
        "handoffs",
        _id(),
        _conversation_fk(),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("priority", sa.Text, nullable=False),
        sa.Column("ticket", JSONB, nullable=False),
        sa.Column("status", sa.Text, nullable=False, server_default="open"),
        _created_at(),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('open', 'resolved')", name="ck_handoffs_status"),
        sa.CheckConstraint("priority IN ('alta', 'normal')", name="ck_handoffs_priority"),
        schema=SCHEMA,
    )
    op.create_index("ix_handoffs_status", "handoffs", ["status", "created_at"], schema=SCHEMA)

    op.create_table(
        "pending_quotes",
        _id(),
        _conversation_fk(),
        sa.Column("request", JSONB, nullable=False),
        sa.Column("attempts", sa.SmallInteger, nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.Text, nullable=False, server_default="waiting"),
        _created_at(),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "status IN ('waiting', 'done', 'expired', 'cancelled')",
            name="ck_pending_quotes_status",
        ),
        schema=SCHEMA,
    )
    # Índice parcial: o worker só procura itens ainda esperando.
    op.create_index(
        "ix_pending_quotes_due",
        "pending_quotes",
        ["next_attempt_at"],
        schema=SCHEMA,
        postgresql_where=sa.text("status = 'waiting'"),
    )


def downgrade() -> None:
    for table in (
        "pending_quotes",
        "handoffs",
        "quote_attempts",
        "quotes",
        "events",
        "messages",
        "conversations",
    ):
        op.drop_table(table, schema=SCHEMA)
    op.execute(f"DROP FUNCTION IF EXISTS {SCHEMA}.events_append_only()")
