"""Implementações Postgres das interfaces de persistência (schema `agent`)."""

from __future__ import annotations

import json
from collections.abc import Mapping
from decimal import Decimal
from typing import Any
from uuid import UUID

from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from autoseguro.store.base import QuoteStatus


def _json_default(value: object) -> object:
    # Valores monetários vão como string para não perder precisão ("209.9", não 209.89999).
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, UUID):
        return str(value)
    raise TypeError(f"Tipo não serializável em JSON: {type(value).__name__}")


def _jsonb(value: Mapping[str, Any]) -> Jsonb:
    return Jsonb(value, dumps=lambda obj: json.dumps(obj, default=_json_default))


class PostgresEventStore:
    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def append(
        self, type: str, payload: Mapping[str, Any], *, conversation_id: UUID | None = None
    ) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(
                "INSERT INTO agent.events (conversation_id, type, payload) VALUES (%s, %s, %s)",
                (conversation_id, type, _jsonb(payload)),
            )


class PostgresQuoteRepository:
    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def create(
        self,
        *,
        quote_id: UUID,
        conversation_id: UUID | None,
        request: Mapping[str, Any],
        cache_key: str,
        from_cache: bool,
    ) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(
                "INSERT INTO agent.quotes"
                " (id, conversation_id, request, status, cache_key, from_cache)"
                " VALUES (%s, %s, %s, 'pending', %s, %s)",
                (quote_id, conversation_id, _jsonb(request), cache_key, from_cache),
            )

    async def add_attempt(
        self,
        *,
        quote_id: UUID,
        attempt_no: int,
        status_code: int | None,
        error_kind: str | None,
        latency_ms: int,
    ) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(
                "INSERT INTO agent.quote_attempts"
                " (quote_id, attempt_no, status_code, error_kind, latency_ms)"
                " VALUES (%s, %s, %s, %s, %s)",
                (quote_id, attempt_no, status_code, error_kind, latency_ms),
            )

    async def finish(
        self, *, quote_id: UUID, status: QuoteStatus, result: Mapping[str, Any] | None
    ) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(
                "UPDATE agent.quotes SET status = %s, result = %s, finished_at = now()"
                " WHERE id = %s",
                (status, _jsonb(result) if result is not None else None, quote_id),
            )
