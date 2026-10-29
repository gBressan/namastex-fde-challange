"""Interfaces de persistência. O domínio e o cliente de cotação dependem só delas."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, Protocol
from uuid import UUID

QuoteStatus = Literal["succeeded", "refused", "invalid", "unavailable"]


class EventStore(Protocol):
    """Registro append-only do que aconteceu. É a base da rastreabilidade."""

    async def append(
        self, type: str, payload: Mapping[str, Any], *, conversation_id: UUID | None = None
    ) -> None: ...


class QuoteRepository(Protocol):
    async def create(
        self,
        *,
        quote_id: UUID,
        conversation_id: UUID | None,
        request: Mapping[str, Any],
        cache_key: str,
        from_cache: bool,
    ) -> None: ...

    async def add_attempt(
        self,
        *,
        quote_id: UUID,
        attempt_no: int,
        status_code: int | None,
        error_kind: str | None,
        latency_ms: int,
    ) -> None: ...

    async def finish(
        self, *, quote_id: UUID, status: QuoteStatus, result: Mapping[str, Any] | None
    ) -> None: ...
