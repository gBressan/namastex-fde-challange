"""QuoteClient gravando no Postgres de verdade: pedido, tentativas e eventos ligados pelo
mesmo quote_id."""

from __future__ import annotations

import random
from datetime import date

import httpx
import psycopg
import pytest
import respx

from autoseguro import db
from autoseguro.config import Settings
from autoseguro.domain.models import PlanId, QuoteRequest
from autoseguro.quote.client import QuoteClient
from autoseguro.quote.outcomes import QuoteSucceeded
from autoseguro.store.postgres import PostgresEventStore, PostgresQuoteRepository

pytestmark = pytest.mark.db

REQ = QuoteRequest(
    plano_id=PlanId.ESSENCIAL,
    idade=30,
    veiculo_ano=2024,
    cep="01310100",
    data_inicio=date(2026, 12, 1),
)
OK_BODY = {
    "plano_id": "essencial",
    "plano_nome": "Essencial",
    "premio_mensal": 119.9,
    "franquia": 4500,
    "coberturas": ["colisao", "roubo", "furto"],
    "multiplicadores": {"faixa_etaria": 1.0, "idade_veiculo": 1.0, "regiao": 1.0},
    "carencia": {"coberturas": ["roubo", "furto"], "dias": 30, "observacao": "..."},
    "moeda": "BRL",
}


async def test_cotacao_fica_rastreavel_no_banco(fresh_database: Settings) -> None:
    db.migrate(fresh_database)

    async with (
        db.open_pool(fresh_database) as pool,
        httpx.AsyncClient(base_url="http://quote-api") as http,
    ):
        client = QuoteClient(
            http=http,
            events=PostgresEventStore(pool),
            quotes=PostgresQuoteRepository(pool),
            rng=random.Random(0),
            sleep=_no_sleep,
        )
        with respx.mock(base_url="http://quote-api") as api:
            api.post("/quote").mock(
                side_effect=[httpx.Response(503, json={}), httpx.Response(200, json=OK_BODY)]
            )
            outcome = await client.quote(REQ)

    assert isinstance(outcome, QuoteSucceeded)
    with psycopg.connect(fresh_database.database_url) as conn:
        quote = conn.execute(
            "SELECT status, result->>'premio_mensal', request->>'cep', finished_at IS NOT NULL"
            " FROM agent.quotes WHERE id = %s",
            (outcome.quote_id,),
        ).fetchone()
        attempts = conn.execute(
            "SELECT attempt_no, status_code, error_kind FROM agent.quote_attempts"
            " WHERE quote_id = %s ORDER BY attempt_no",
            (outcome.quote_id,),
        ).fetchall()
        events = conn.execute(
            "SELECT type FROM agent.events WHERE payload->>'quote_id' = %s ORDER BY id",
            (str(outcome.quote_id),),
        ).fetchall()

    # O pedido completo fica na tabela (é dado de negócio, usado no retry diferido);
    # nos eventos, só o prefixo do CEP.
    assert quote == ("succeeded", "119.9", "01310-100", True)
    assert attempts == [(1, 503, "server_error"), (2, 200, None)]
    assert [e[0] for e in events] == [
        "quote_requested",
        "quote_attempt",
        "quote_attempt",
        "quote_succeeded",
    ]


async def _no_sleep(_: float) -> None:
    return None
