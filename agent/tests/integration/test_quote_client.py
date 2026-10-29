"""QuoteClient contra a API simulada com respx: cada tipo de falha e o que o cliente faz.

O relógio é falso: `sleep` só avança o tempo, então os testes não esperam de verdade.
"""

from __future__ import annotations

import json
import random
from collections.abc import Callable, Iterator
from datetime import date
from typing import Any

import httpx
import pytest
import respx

from autoseguro.domain.models import PlanId, QuoteRequest
from autoseguro.quote.breaker import CircuitBreaker
from autoseguro.quote.cache import QuoteCache
from autoseguro.quote.client import QuoteClient, QuoteClientConfig
from autoseguro.quote.outcomes import (
    QuoteInvalid,
    QuoteRefused,
    QuoteSucceeded,
    QuoteUnavailable,
)
from autoseguro.store.memory import InMemoryEventStore, InMemoryQuoteRepository

BASE_URL = "http://quote-api"
REQ = QuoteRequest(
    plano_id=PlanId.COMPLETO,
    idade=35,
    veiculo_ano=2022,
    cep="21310100",
    data_inicio=date(2026, 11, 15),
)
OK_BODY: dict[str, Any] = {
    "plano_id": "completo",
    "plano_nome": "Completo",
    "premio_mensal": 272.87,
    "franquia": 3000,
    "coberturas": ["colisao", "roubo", "furto", "terceiros", "vidros"],
    "multiplicadores": {"faixa_etaria": 1.0, "idade_veiculo": 1.0, "regiao": 1.3},
    "carencia": {"coberturas": ["roubo", "furto"], "dias": 30, "observacao": "..."},
    "moeda": "BRL",
    "primeiro_pagamento_pro_rata": {
        "dias_no_mes": 30,
        "dias_cobrados": 16,
        "valor_primeiro_pagamento": 145.53,
    },
}
UNAVAILABLE_BODY = {"error": "upstream_unavailable", "message": "Tente novamente."}


def ok() -> httpx.Response:
    return httpx.Response(200, json=OK_BODY)


def fail(status: int = 503) -> httpx.Response:
    return httpx.Response(status, json=UNAVAILABLE_BODY)


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class Harness:
    def __init__(self, config: QuoteClientConfig | None = None) -> None:
        self.clock = FakeClock()
        self.events = InMemoryEventStore()
        self.quotes = InMemoryQuoteRepository()
        self.http = httpx.AsyncClient(base_url=BASE_URL)
        self.client = QuoteClient(
            http=self.http,
            events=self.events,
            quotes=self.quotes,
            config=config,
            breaker=CircuitBreaker(clock=self.clock),
            cache=QuoteCache(clock=self.clock),
            clock=self.clock,
            sleep=self.clock.sleep,
            rng=random.Random(42),
            today=lambda: date(2026, 10, 29),
        )

    def timeout_after(self, seconds: float) -> Callable[[httpx.Request], httpx.Response]:
        """Simula uma chamada que estoura o timeout depois de `seconds` segundos."""

        def side_effect(request: httpx.Request) -> httpx.Response:
            self.clock.now += seconds
            raise httpx.ReadTimeout("timeout", request=request)

        return side_effect

    def event_types(self) -> list[str]:
        return [e.type for e in self.events.events]


@pytest.fixture
def api() -> Iterator[respx.MockRouter]:
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
        yield router


async def test_falhas_transitorias_e_depois_sucesso(api: respx.MockRouter) -> None:
    h = Harness()
    api.post("/quote").mock(side_effect=[fail(503), fail(502), ok()])

    outcome = await h.client.quote(REQ)

    assert isinstance(outcome, QuoteSucceeded)
    assert outcome.attempts == 3
    assert str(outcome.result.premio_mensal) == "272.87"
    assert len(h.clock.sleeps) == 2
    # Full jitter: cada espera fica entre 0 e o teto da tentativa (0,3 s, depois 0,6 s).
    assert 0 <= h.clock.sleeps[0] <= 0.3
    assert 0 <= h.clock.sleeps[1] <= 0.6
    stored = h.quotes.quotes[outcome.quote_id]
    assert stored.status == "succeeded"
    assert [a.status_code for a in stored.attempts] == [503, 502, 200]
    assert h.event_types() == [
        "quote_requested",
        "quote_attempt",
        "quote_attempt",
        "quote_attempt",
        "quote_succeeded",
    ]


async def test_timeout_e_depois_sucesso(api: respx.MockRouter) -> None:
    h = Harness()
    api.post("/quote").mock(side_effect=[h.timeout_after(2.5), ok()])  # type: ignore[list-item]

    outcome = await h.client.quote(REQ)

    assert isinstance(outcome, QuoteSucceeded)
    assert outcome.attempts == 2
    attempts = h.quotes.quotes[outcome.quote_id].attempts
    assert attempts[0].error_kind == "timeout"
    assert attempts[0].status_code is None
    assert attempts[0].latency_ms == 2500


async def test_quatro_falhas_esgotam_as_tentativas(api: respx.MockRouter) -> None:
    h = Harness()
    route = api.post("/quote").mock(side_effect=[fail(500)] * 4)

    outcome = await h.client.quote(REQ)

    assert isinstance(outcome, QuoteUnavailable)
    assert outcome.reason == "retries_exhausted"
    assert outcome.attempts == 4
    assert route.call_count == 4
    assert h.quotes.quotes[outcome.quote_id].status == "unavailable"


async def test_recusa_nao_tem_retry_e_vai_para_o_cache(api: respx.MockRouter) -> None:
    h = Harness()
    route = api.post("/quote").mock(
        return_value=httpx.Response(
            422, json={"error": "cotacao_recusada", "motivo": "Idade acima do limite (75 anos)."}
        )
    )

    first = await h.client.quote(REQ)
    second = await h.client.quote(REQ)

    assert isinstance(first, QuoteRefused)
    assert first.attempts == 1
    assert first.motivo == "Idade acima do limite (75 anos)."
    assert isinstance(second, QuoteRefused)
    assert second.from_cache
    assert route.call_count == 1
    assert h.clock.sleeps == []


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(422, json={"detail": [{"loc": ["body", "idade"], "msg": "required"}]}),
        httpx.Response(422, json={"error": "cotacao_recusada", "motivo": "Plano 'x' inexistente."}),
        httpx.Response(400, json={"error": "payload_invalido", "detalhe": "Invalid isoformat"}),
    ],
    ids=["422-validacao-fastapi", "422-plano-inexistente", "400"],
)
async def test_pedido_invalido_nao_tem_retry(
    api: respx.MockRouter, response: httpx.Response
) -> None:
    h = Harness()
    route = api.post("/quote").mock(return_value=response)

    outcome = await h.client.quote(REQ)

    assert isinstance(outcome, QuoteInvalid)
    assert outcome.attempts == 1
    assert route.call_count == 1
    assert "quote_invalid" in h.event_types()


async def test_resposta_200_fora_do_contrato_tem_retry(api: respx.MockRouter) -> None:
    h = Harness()
    api.post("/quote").mock(side_effect=[httpx.Response(200, json={"premio": "?"}), ok()])

    outcome = await h.client.quote(REQ)

    assert isinstance(outcome, QuoteSucceeded)
    assert h.quotes.quotes[outcome.quote_id].attempts[0].error_kind == "bad_response"


async def test_prazo_total_interrompe_antes_do_limite_de_tentativas(
    api: respx.MockRouter,
) -> None:
    h = Harness(QuoteClientConfig(timeout_s=2.5, deadline_s=2.6))
    route = api.post("/quote").mock(side_effect=h.timeout_after(2.5))

    outcome = await h.client.quote(REQ)

    assert isinstance(outcome, QuoteUnavailable)
    assert outcome.reason == "deadline"
    assert route.call_count == 1


async def test_timeout_da_tentativa_nunca_passa_do_prazo_total(api: respx.MockRouter) -> None:
    h = Harness(QuoteClientConfig(timeout_s=2.5, deadline_s=4.0))
    read_timeouts: list[float] = []

    def slow(request: httpx.Request) -> httpx.Response:
        read_timeouts.append(request.extensions["timeout"]["read"])
        h.clock.now += 2.5
        raise httpx.ReadTimeout("timeout", request=request)

    api.post("/quote").mock(side_effect=slow)

    outcome = await h.client.quote(REQ)

    assert isinstance(outcome, QuoteUnavailable)
    assert read_timeouts[0] == 2.5
    # A segunda tentativa só tem o que sobra do prazo (4.0 - 2.5 - backoff).
    assert len(read_timeouts) == 2
    assert read_timeouts[1] < 1.5


async def test_breaker_abre_bloqueia_e_fecha(api: respx.MockRouter) -> None:
    h = Harness()
    route = api.post("/quote").mock(side_effect=[fail(503)] * 5 + [ok()])

    first = await h.client.quote(REQ)  # 4 falhas
    second = await h.client.quote(REQ)  # 5ª falha abre o breaker
    third = await h.client.quote(REQ)  # aberto: nem chama a API

    assert isinstance(first, QuoteUnavailable) and first.reason == "retries_exhausted"
    assert isinstance(second, QuoteUnavailable) and second.reason == "circuit_open"
    assert second.attempts == 1
    assert isinstance(third, QuoteUnavailable) and third.reason == "circuit_open"
    assert third.attempts == 0
    assert route.call_count == 5

    h.clock.now += 30  # passa o tempo de espera: meio-aberto libera uma chamada de teste
    fourth = await h.client.quote(REQ)

    assert isinstance(fourth, QuoteSucceeded)
    transitions = [
        (e.payload["from"], e.payload["to"]) for e in h.events.of_type("breaker_state_changed")
    ]
    assert transitions == [("closed", "open"), ("open", "half_open"), ("half_open", "closed")]


async def test_recusa_conta_como_api_de_pe_para_o_breaker(api: respx.MockRouter) -> None:
    h = Harness()
    refused = httpx.Response(422, json={"error": "cotacao_recusada", "motivo": "Idade."})
    api.post("/quote").mock(side_effect=[fail()] * 4 + [refused, fail(), ok()])

    await h.client.quote(REQ)  # 4 falhas seguidas
    await h.client.quote(REQ.model_copy(update={"idade": 80}))  # recusa: a API respondeu
    outcome = await h.client.quote(REQ.model_copy(update={"idade": 40}))

    # Sem zerar a contagem na recusa, a falha seguinte seria a 5ª e abriria o breaker.
    assert isinstance(outcome, QuoteSucceeded)
    assert outcome.attempts == 2
    assert h.client.breaker_state == "closed"


async def test_retry_after_e_respeitado(api: respx.MockRouter) -> None:
    h = Harness()
    api.post("/quote").mock(side_effect=[httpx.Response(429, headers={"Retry-After": "1.5"}), ok()])

    outcome = await h.client.quote(REQ)

    assert isinstance(outcome, QuoteSucceeded)
    assert h.clock.sleeps[0] >= 1.5


async def test_sucesso_vai_para_o_cache_e_falha_nao(api: respx.MockRouter) -> None:
    h = Harness()
    route = api.post("/quote").mock(side_effect=[fail()] * 4 + [ok()])

    unavailable = await h.client.quote(REQ)
    succeeded = await h.client.quote(REQ)  # falha não foi para o cache: chama a API
    cached = await h.client.quote(REQ)

    assert isinstance(unavailable, QuoteUnavailable)
    assert isinstance(succeeded, QuoteSucceeded) and not succeeded.from_cache
    assert isinstance(cached, QuoteSucceeded) and cached.from_cache and cached.attempts == 0
    assert route.call_count == 5
    assert len({unavailable.quote_id, succeeded.quote_id, cached.quote_id}) == 3


async def test_evento_de_pedido_mascara_o_cep(api: respx.MockRouter) -> None:
    h = Harness()
    api.post("/quote").mock(return_value=ok())

    await h.client.quote(REQ)

    requested = h.events.of_type("quote_requested")[0]
    assert requested.payload["request"]["cep"] == "21***-***"
    assert "21310100" not in str(h.events.events)


async def test_payload_enviado_no_formato_da_api(api: respx.MockRouter) -> None:
    h = Harness()
    route = api.post("/quote").mock(return_value=ok())

    await h.client.quote(REQ)

    assert json.loads(route.calls.last.request.content) == {
        "plano_id": "completo",
        "idade": 35,
        "veiculo_ano": 2022,
        "cep": "21310-100",
        "data_inicio": "2026-11-15",
    }
