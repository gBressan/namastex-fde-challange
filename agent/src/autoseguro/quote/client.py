"""Cliente resiliente da `POST /quote`.

Garantias:
- nunca trava: cada tentativa tem timeout, e a cotação inteira tem um prazo total;
- nunca inventa valor: devolve sempre um `QuoteOutcome`, e só `QuoteSucceeded` traz preço;
- tudo fica registrado: o pedido, cada tentativa (status, latência) e o desfecho, com o
  mesmo `quote_id`.

O retry acontece só aqui. Quem chama (grafo, worker) não repete a mesma chamada.
"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date
from typing import Any
from uuid import UUID, uuid4

import httpx
from pydantic import ValidationError

from autoseguro.config import Settings
from autoseguro.domain.models import QuoteRequest, QuoteResult
from autoseguro.domain.normalizers import mask_cep_for_log
from autoseguro.log import get_logger
from autoseguro.quote.breaker import BreakerState, CircuitBreaker
from autoseguro.quote.cache import CachedRefusal, CachedSuccess, QuoteCache, cache_key
from autoseguro.quote.errors import RETRYABLE, ErrorKind, classify_exception, classify_response
from autoseguro.quote.outcomes import (
    QuoteInvalid,
    QuoteOutcome,
    QuoteRefused,
    QuoteSucceeded,
    QuoteUnavailable,
    UnavailableReason,
)
from autoseguro.store.base import EventStore, QuoteRepository

log = get_logger(__name__)


@dataclass(frozen=True)
class QuoteClientConfig:
    timeout_s: float = 2.5
    connect_timeout_s: float = 1.0
    max_attempts: int = 4
    deadline_s: float = 12.0
    backoff_base_s: float = 0.3
    backoff_cap_s: float = 2.0
    # Abaixo disso não vale começar uma tentativa: ela não teria tempo de responder.
    min_attempt_s: float = 0.2

    @classmethod
    def from_settings(cls, settings: Settings) -> QuoteClientConfig:
        return cls(
            timeout_s=settings.quote_timeout_s,
            max_attempts=settings.quote_max_attempts,
            deadline_s=settings.quote_deadline_s,
        )


@dataclass(frozen=True)
class _Attempt:
    kind: ErrorKind | None
    status_code: int | None
    body: Any = None
    result: QuoteResult | None = None
    retry_after_s: float | None = None
    detail: str = ""


class QuoteClient:
    def __init__(
        self,
        *,
        http: httpx.AsyncClient,
        events: EventStore,
        quotes: QuoteRepository,
        config: QuoteClientConfig | None = None,
        breaker: CircuitBreaker | None = None,
        cache: QuoteCache | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        rng: random.Random | None = None,
        today: Callable[[], date] = date.today,
    ) -> None:
        self._http = http
        self._events = events
        self._quotes = quotes
        self._config = config or QuoteClientConfig()
        self._breaker = breaker or CircuitBreaker(clock=clock)
        self._cache = cache or QuoteCache(clock=clock)
        self._clock = clock
        self._sleep = sleep
        self._rng = rng or random.Random()
        self._today = today

    @property
    def breaker_state(self) -> BreakerState:
        return self._breaker.state

    async def quote(
        self, req: QuoteRequest, *, conversation_id: UUID | None = None
    ) -> QuoteOutcome:
        quote_id = uuid4()
        key = cache_key(req, self._today())
        cached = self._cache.get(key)

        await self._quotes.create(
            quote_id=quote_id,
            conversation_id=conversation_id,
            request=req.to_api_payload(),
            cache_key=key,
            from_cache=cached is not None,
        )
        await self._event(
            "quote_requested",
            conversation_id,
            quote_id=quote_id,
            request={**req.to_api_payload(), "cep": mask_cep_for_log(req.cep)},
            cache_key=key[:12],
        )

        if isinstance(cached, CachedSuccess):
            return await self._succeeded(quote_id, conversation_id, cached.result, 0, True)
        if isinstance(cached, CachedRefusal):
            return await self._refused(quote_id, conversation_id, cached.motivo, 0, True)

        return await self._call_with_retries(req, quote_id, conversation_id, key)

    async def _call_with_retries(
        self, req: QuoteRequest, quote_id: UUID, conversation_id: UUID | None, key: str
    ) -> QuoteOutcome:
        cfg = self._config
        start = self._clock()
        attempt_no = 0

        while True:
            if not await self._breaker_allows():
                return await self._unavailable(
                    quote_id, conversation_id, "circuit_open", attempt_no, start
                )

            remaining = cfg.deadline_s - (self._clock() - start)
            if remaining < cfg.min_attempt_s:
                return await self._unavailable(
                    quote_id, conversation_id, "deadline", attempt_no, start
                )

            attempt_no += 1
            # O timeout nunca passa do prazo total: uma tentativa que começa aos 11 s não
            # pode terminar aos 13,5 s.
            timeout = min(cfg.timeout_s, remaining)
            t0 = self._clock()
            attempt = await self._attempt(req, timeout)
            latency_ms = round((self._clock() - t0) * 1000)

            await self._quotes.add_attempt(
                quote_id=quote_id,
                attempt_no=attempt_no,
                status_code=attempt.status_code,
                error_kind=attempt.kind.value if attempt.kind else None,
                latency_ms=latency_ms,
            )
            await self._event(
                "quote_attempt",
                conversation_id,
                quote_id=quote_id,
                attempt_no=attempt_no,
                status_code=attempt.status_code,
                error_kind=attempt.kind.value if attempt.kind else None,
                latency_ms=latency_ms,
            )

            if attempt.kind is None and attempt.result is not None:
                await self._breaker_success()
                self._cache.put(key, CachedSuccess(attempt.result))
                return await self._succeeded(
                    quote_id, conversation_id, attempt.result, attempt_no, False
                )

            if attempt.kind is ErrorKind.REFUSED:
                # A API respondeu: está de pé.
                await self._breaker_success()
                motivo = str(attempt.body.get("motivo", "")) if attempt.body else ""
                self._cache.put(key, CachedRefusal(motivo))
                return await self._refused(quote_id, conversation_id, motivo, attempt_no, False)

            if attempt.kind not in RETRYABLE:
                await self._breaker_success()
                return await self._invalid(quote_id, conversation_id, attempt, attempt_no)

            await self._breaker_failure()
            if attempt_no >= cfg.max_attempts:
                return await self._unavailable(
                    quote_id, conversation_id, "retries_exhausted", attempt_no, start
                )
            if self._breaker.state is BreakerState.OPEN:
                # Esta falha abriu o breaker: não adianta esperar o backoff.
                return await self._unavailable(
                    quote_id, conversation_id, "circuit_open", attempt_no, start
                )

            delay = self._backoff(attempt_no, attempt.retry_after_s)
            if (self._clock() - start) + delay + cfg.min_attempt_s > cfg.deadline_s:
                return await self._unavailable(
                    quote_id, conversation_id, "deadline", attempt_no, start
                )
            await self._sleep(delay)

    async def _attempt(self, req: QuoteRequest, timeout: float) -> _Attempt:
        http_timeout = httpx.Timeout(timeout, connect=min(self._config.connect_timeout_s, timeout))
        try:
            # httpx mede o timeout por operação; o asyncio.timeout garante o tempo total.
            async with asyncio.timeout(timeout):
                response = await self._http.post(
                    "/quote", json=req.to_api_payload(), timeout=http_timeout
                )
        except (TimeoutError, httpx.TransportError) as exc:
            return _Attempt(kind=classify_exception(exc), status_code=None, detail=repr(exc))

        try:
            body = response.json()
        except ValueError:
            body = None

        kind = classify_response(response.status_code, body)
        if kind is None:
            try:
                return _Attempt(kind=None, status_code=200, result=QuoteResult.model_validate(body))
            except ValidationError as exc:
                return _Attempt(
                    kind=ErrorKind.BAD_RESPONSE, status_code=200, body=body, detail=str(exc)
                )

        return _Attempt(
            kind=kind,
            status_code=response.status_code,
            body=body,
            retry_after_s=_retry_after(response),
            detail=str(body)[:500],
        )

    def _backoff(self, attempt_no: int, retry_after_s: float | None) -> float:
        """Exponencial com *full jitter*: um valor aleatório entre 0 e o teto da tentativa.
        O jitter evita que vários clientes tentem de novo no mesmo instante."""
        cfg = self._config
        ceiling = min(cfg.backoff_cap_s, cfg.backoff_base_s * 2 ** (attempt_no - 1))
        delay = self._rng.uniform(0, ceiling)
        if retry_after_s is not None:
            delay = max(delay, retry_after_s)
        return delay

    # --- desfechos -------------------------------------------------------------------

    async def _succeeded(
        self,
        quote_id: UUID,
        conversation_id: UUID | None,
        result: QuoteResult,
        attempts: int,
        from_cache: bool,
    ) -> QuoteSucceeded:
        await self._quotes.finish(
            quote_id=quote_id, status="succeeded", result=result.model_dump(mode="json")
        )
        await self._event(
            "quote_succeeded",
            conversation_id,
            quote_id=quote_id,
            plano_id=result.plano_id.value,
            premio_mensal=result.premio_mensal,
            attempts=attempts,
            from_cache=from_cache,
        )
        return QuoteSucceeded(quote_id, result, attempts, from_cache)

    async def _refused(
        self,
        quote_id: UUID,
        conversation_id: UUID | None,
        motivo: str,
        attempts: int,
        from_cache: bool,
    ) -> QuoteRefused:
        await self._quotes.finish(quote_id=quote_id, status="refused", result={"motivo": motivo})
        await self._event(
            "quote_refused",
            conversation_id,
            quote_id=quote_id,
            motivo=motivo,
            attempts=attempts,
            from_cache=from_cache,
        )
        return QuoteRefused(quote_id, motivo, attempts, from_cache)

    async def _invalid(
        self, quote_id: UUID, conversation_id: UUID | None, attempt: _Attempt, attempts: int
    ) -> QuoteInvalid:
        log.error(
            "quote_invalid_request",
            quote_id=str(quote_id),
            status_code=attempt.status_code,
            detail=attempt.detail,
        )
        await self._quotes.finish(
            quote_id=quote_id, status="invalid", result={"detalhe": attempt.detail}
        )
        await self._event(
            "quote_invalid",
            conversation_id,
            quote_id=quote_id,
            status_code=attempt.status_code,
            detalhe=attempt.detail,
            attempts=attempts,
        )
        return QuoteInvalid(quote_id, attempt.detail, attempts)

    async def _unavailable(
        self,
        quote_id: UUID,
        conversation_id: UUID | None,
        reason: UnavailableReason,
        attempts: int,
        start: float,
    ) -> QuoteUnavailable:
        elapsed_ms = round((self._clock() - start) * 1000)
        await self._quotes.finish(quote_id=quote_id, status="unavailable", result=None)
        await self._event(
            "quote_unavailable",
            conversation_id,
            quote_id=quote_id,
            reason=reason,
            attempts=attempts,
            elapsed_ms=elapsed_ms,
        )
        return QuoteUnavailable(quote_id, reason, attempts, elapsed_ms)

    # --- breaker e eventos -----------------------------------------------------------

    async def _breaker_allows(self) -> bool:
        before = self._breaker.state
        allowed = self._breaker.allow()
        await self._breaker_changed(before)
        return allowed

    async def _breaker_success(self) -> None:
        before = self._breaker.state
        self._breaker.record_success()
        await self._breaker_changed(before)

    async def _breaker_failure(self) -> None:
        before = self._breaker.state
        self._breaker.record_failure()
        await self._breaker_changed(before)

    async def _breaker_changed(self, before: BreakerState) -> None:
        after = self._breaker.state
        if after is not before:
            log.warning("breaker_state_changed", from_state=before.value, to_state=after.value)
            await self._events.append(
                "breaker_state_changed", {"from": before.value, "to": after.value}
            )

    async def _event(self, type: str, conversation_id: UUID | None, **payload: Any) -> None:
        fields = {k: str(v) if isinstance(v, UUID) else v for k, v in payload.items()}
        log.info(type, conversation_id=str(conversation_id) if conversation_id else None, **fields)
        await self._events.append(type, payload, conversation_id=conversation_id)


def _retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("retry-after")
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None
