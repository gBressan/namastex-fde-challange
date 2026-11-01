"""Compara a quote-api real com o oráculo de preço, caso a caso.

Precisa da quote-api rodando (`docker compose up -d quote-api`). Usa o QuoteClient com o caos
padrão ligado, então também exercita o retry. Os anos são relativos a hoje: com anos fixos,
os testes de idade do veículo quebrariam na virada do ano.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import httpx
import pytest

from autoseguro.domain.models import PlanId, QuoteRequest
from autoseguro.quote.cache import QuoteCache
from autoseguro.quote.client import QuoteClient
from autoseguro.quote.outcomes import QuoteOutcome, QuoteRefused, QuoteSucceeded, QuoteUnavailable
from autoseguro.store.memory import InMemoryEventStore, InMemoryQuoteRepository
from tests.oracle import OracleQuote, OracleRefusal, expected_quote

pytestmark = pytest.mark.api

TODAY = date.today()
Y = TODAY.year
NEXT_MONTH_1 = (TODAY.replace(day=1) + timedelta(days=32)).replace(day=1)
MID_MONTH = NEXT_MONTH_1.replace(day=15)

CASES = [
    # (plano, idade, ano do veículo, cep, início)
    ("essencial", 35, Y - 1, "01310100", NEXT_MONTH_1),
    ("completo", 20, Y - 2, "01310100", MID_MONTH),  # faixa 18-24
    ("premium", 27, Y - 3, "04567000", MID_MONTH),  # faixa 25-29
    ("completo", 59, Y - 5, "13000000", NEXT_MONTH_1),  # limite 30-59
    ("essencial", 60, Y - 6, "30100000", MID_MONTH),  # faixa 60-75, veículo 6-10
    ("premium", 75, Y - 10, "41000000", MID_MONTH),  # limites
    ("completo", 40, Y - 11, "80000000", MID_MONTH),  # veículo 11-20
    ("completo", 40, Y - 20, "88000000", MID_MONTH),  # limite 20 anos
    ("completo", 35, Y - 2, "07123456", MID_MONTH),  # CEP de risco 07
    ("completo", 35, Y - 2, "08123456", MID_MONTH),  # 08
    ("essencial", 35, Y - 2, "21310100", MID_MONTH),  # 21
    ("premium", 35, Y - 2, "26703384", MID_MONTH),  # 26
    ("completo", 35, Y - 2, "59000000", MID_MONTH),  # 59
    ("completo", 76, Y - 2, "01310100", MID_MONTH),  # recusa: idade
    ("completo", 17, Y - 2, "01310100", MID_MONTH),  # recusa: menor de 18
    ("completo", 35, Y - 21, "01310100", MID_MONTH),  # recusa: veículo > 20 anos
    ("completo", 35, Y + 1, "01310100", MID_MONTH),  # recusa: ano-modelo futuro
]


async def _quote_until_answer(http: httpx.AsyncClient, req: QuoteRequest) -> QuoteOutcome:
    """Com o caos padrão, ~0,8% das cotações ficam indisponíveis mesmo com o retry do cliente.
    Aqui o que importa é o preço, então tentamos de novo, **com um cliente novo a cada vez**:
    depois de 4 falhas seguidas, a próxima abre o breaker, e o mesmo cliente devolveria
    `circuit_open` sem nem chamar a API (o que deixava este teste instável na CI)."""
    for _ in range(3):
        client = QuoteClient(
            http=http,
            events=InMemoryEventStore(),
            quotes=InMemoryQuoteRepository(),
            cache=QuoteCache(ttl_s=0),
        )
        outcome = await client.quote(req)
        if not isinstance(outcome, QuoteUnavailable):
            return outcome
    return outcome


@pytest.mark.parametrize(("plano", "idade", "ano", "cep", "inicio"), CASES)
async def test_api_bate_com_o_oraculo(
    quote_api_url: str, plano: str, idade: int, ano: int, cep: str, inicio: date
) -> None:
    req = QuoteRequest(
        plano_id=PlanId(plano), idade=idade, veiculo_ano=ano, cep=cep, data_inicio=inicio
    )
    expected = expected_quote(
        plano_id=plano,
        idade=idade,
        veiculo_ano=ano,
        cep=req.to_api_payload()["cep"],
        data_inicio=inicio,
        today=TODAY,
    )

    async with httpx.AsyncClient(base_url=quote_api_url) as http:
        outcome = await _quote_until_answer(http, req)

    if isinstance(expected, OracleRefusal):
        assert isinstance(outcome, QuoteRefused), outcome
        assert outcome.motivo == expected.motivo
        return

    assert isinstance(expected, OracleQuote)
    assert isinstance(outcome, QuoteSucceeded), outcome
    assert outcome.result.premio_mensal == Decimal(str(expected.premio_mensal))
    assert outcome.result.franquia == Decimal(str(expected.franquia))
    pro_rata = outcome.result.primeiro_pagamento_pro_rata
    if expected.valor_primeiro_pagamento is None:
        assert pro_rata is None
    else:
        assert pro_rata is not None
        assert pro_rata.valor_primeiro_pagamento == Decimal(str(expected.valor_primeiro_pagamento))
