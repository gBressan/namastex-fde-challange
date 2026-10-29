"""Implementações em memória das interfaces de persistência, para testes e para rodar sem
banco (`autoseguro quote --no-db`)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from autoseguro.store.base import QuoteStatus


@dataclass
class StoredEvent:
    type: str
    payload: dict[str, Any]
    conversation_id: UUID | None


class InMemoryEventStore:
    def __init__(self) -> None:
        self.events: list[StoredEvent] = []

    async def append(
        self, type: str, payload: Mapping[str, Any], *, conversation_id: UUID | None = None
    ) -> None:
        self.events.append(StoredEvent(type, dict(payload), conversation_id))

    def of_type(self, type: str) -> list[StoredEvent]:
        return [e for e in self.events if e.type == type]


@dataclass
class StoredAttempt:
    attempt_no: int
    status_code: int | None
    error_kind: str | None
    latency_ms: int


@dataclass
class StoredQuote:
    quote_id: UUID
    conversation_id: UUID | None
    request: dict[str, Any]
    cache_key: str
    from_cache: bool
    status: str = "pending"
    result: dict[str, Any] | None = None
    attempts: list[StoredAttempt] = field(default_factory=list)


class InMemoryQuoteRepository:
    def __init__(self) -> None:
        self.quotes: dict[UUID, StoredQuote] = {}

    async def create(
        self,
        *,
        quote_id: UUID,
        conversation_id: UUID | None,
        request: Mapping[str, Any],
        cache_key: str,
        from_cache: bool,
    ) -> None:
        self.quotes[quote_id] = StoredQuote(
            quote_id, conversation_id, dict(request), cache_key, from_cache
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
        self.quotes[quote_id].attempts.append(
            StoredAttempt(attempt_no, status_code, error_kind, latency_ms)
        )

    async def finish(
        self, *, quote_id: UUID, status: QuoteStatus, result: Mapping[str, Any] | None
    ) -> None:
        quote = self.quotes[quote_id]
        quote.status = status
        quote.result = dict(result) if result is not None else None
