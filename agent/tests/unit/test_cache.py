from __future__ import annotations

from datetime import date

from autoseguro.domain.models import PlanId, QuoteRequest
from autoseguro.quote.cache import CachedRefusal, QuoteCache, cache_key

REQ = QuoteRequest(
    plano_id=PlanId.COMPLETO,
    idade=35,
    veiculo_ano=2022,
    cep="01310100",
    data_inicio=date(2026, 11, 15),
)


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_chave_e_estavel_e_depende_do_pedido() -> None:
    today = date(2026, 10, 29)
    assert cache_key(REQ, today) == cache_key(REQ.model_copy(), today)
    assert cache_key(REQ, today) != cache_key(REQ.model_copy(update={"idade": 36}), today)


def test_chave_muda_com_a_data_de_hoje() -> None:
    # A API calcula a idade do veículo com a data atual: na virada do ano o resultado muda.
    assert cache_key(REQ, date(2026, 12, 31)) != cache_key(REQ, date(2027, 1, 1))


def test_entrada_expira_no_ttl() -> None:
    clock = Clock()
    cache = QuoteCache(ttl_s=900, clock=clock)
    cache.put("k", CachedRefusal("Idade acima do limite"))
    clock.now = 899.9
    assert cache.get("k") == CachedRefusal("Idade acima do limite")
    clock.now = 900.0
    assert cache.get("k") is None


def test_ttl_zero_desliga_o_cache() -> None:
    cache = QuoteCache(ttl_s=0, clock=Clock())
    cache.put("k", CachedRefusal("x"))
    assert cache.get("k") is None
