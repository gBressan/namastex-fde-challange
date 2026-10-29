"""Testes de caos contra a quote-api real, com tempo real.

Rodam só quando CHAOS_MODE diz em que modo a quote-api foi subida:

    docker compose -f docker-compose.yml -f docker-compose.chaos-down.yml up -d quote-api
    CHAOS_MODE=down uv run pytest -m chaos

    docker compose -f docker-compose.yml -f docker-compose.chaos-slow.yml up -d quote-api
    CHAOS_MODE=slow uv run pytest -m chaos

    docker compose up -d quote-api   # volta ao normal
"""

from __future__ import annotations

import os
import time
from datetime import date, timedelta

import httpx
import pytest

from autoseguro.domain.models import PlanId, QuoteRequest
from autoseguro.quote.cache import QuoteCache
from autoseguro.quote.client import QuoteClient
from autoseguro.quote.outcomes import QuoteUnavailable
from autoseguro.store.memory import InMemoryEventStore, InMemoryQuoteRepository

pytestmark = pytest.mark.chaos

MODE = os.getenv("CHAOS_MODE")
REQ = QuoteRequest(
    plano_id=PlanId.COMPLETO,
    idade=35,
    veiculo_ano=date.today().year - 2,
    cep="01310100",
    data_inicio=date.today() + timedelta(days=1),
)


def _client(http: httpx.AsyncClient) -> tuple[QuoteClient, InMemoryQuoteRepository]:
    quotes = InMemoryQuoteRepository()
    client = QuoteClient(
        http=http, events=InMemoryEventStore(), quotes=quotes, cache=QuoteCache(ttl_s=0)
    )
    return client, quotes


@pytest.mark.skipif(MODE != "down", reason="precisa da quote-api com chaos-down")
async def test_api_fora_do_ar_abre_o_breaker_e_falha_rapido(quote_api_url: str) -> None:
    async with httpx.AsyncClient(base_url=quote_api_url) as http:
        client, _ = _client(http)

        first = await client.quote(REQ)
        second = await client.quote(REQ)
        t0 = time.monotonic()
        third = await client.quote(REQ)
        third_ms = (time.monotonic() - t0) * 1000

    assert isinstance(first, QuoteUnavailable) and first.reason == "retries_exhausted"
    assert first.attempts == 4
    assert isinstance(second, QuoteUnavailable) and second.reason == "circuit_open"
    assert isinstance(third, QuoteUnavailable) and third.reason == "circuit_open"
    assert third.attempts == 0
    assert third_ms < 50


@pytest.mark.skipif(MODE != "slow", reason="precisa da quote-api com chaos-slow")
async def test_api_lenta_corta_cada_tentativa_e_respeita_o_prazo(quote_api_url: str) -> None:
    async with httpx.AsyncClient(base_url=quote_api_url) as http:
        client, quotes = _client(http)
        t0 = time.monotonic()
        outcome = await client.quote(REQ)
        elapsed = time.monotonic() - t0

    assert isinstance(outcome, QuoteUnavailable)
    assert outcome.reason in ("deadline", "retries_exhausted")
    assert elapsed < 12.5
    attempts = quotes.quotes[outcome.quote_id].attempts
    assert all(a.error_kind == "timeout" for a in attempts)
    assert all(a.latency_ms <= 2600 for a in attempts)
