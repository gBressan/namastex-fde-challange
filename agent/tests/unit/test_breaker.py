from __future__ import annotations

from autoseguro.quote.breaker import BreakerState, CircuitBreaker


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_abre_apos_cinco_falhas_consecutivas() -> None:
    breaker = CircuitBreaker(failure_threshold=5, clock=Clock())
    for _ in range(4):
        breaker.record_failure()
    assert breaker.state is BreakerState.CLOSED
    assert breaker.allow()
    breaker.record_failure()
    assert breaker.state is BreakerState.OPEN
    assert not breaker.allow()


def test_sucesso_zera_a_contagem() -> None:
    breaker = CircuitBreaker(failure_threshold=5, clock=Clock())
    for _ in range(4):
        breaker.record_failure()
    breaker.record_success()
    for _ in range(4):
        breaker.record_failure()
    assert breaker.state is BreakerState.CLOSED


def test_meio_aberto_libera_uma_chamada_e_fecha_no_sucesso() -> None:
    clock = Clock()
    breaker = CircuitBreaker(failure_threshold=1, reset_timeout_s=30, clock=clock)
    breaker.record_failure()
    clock.now = 29.9
    assert not breaker.allow()
    clock.now = 30.0
    assert breaker.allow()
    assert breaker.state is BreakerState.HALF_OPEN
    assert not breaker.allow()  # só uma chamada de teste por vez
    breaker.record_success()
    assert breaker.state is BreakerState.CLOSED
    assert breaker.allow()


def test_meio_aberto_volta_a_abrir_na_falha() -> None:
    clock = Clock()
    breaker = CircuitBreaker(failure_threshold=1, reset_timeout_s=30, clock=clock)
    breaker.record_failure()
    clock.now = 30.0
    assert breaker.allow()
    breaker.record_failure()
    assert breaker.state is BreakerState.OPEN
    clock.now = 59.0
    assert not breaker.allow()  # a espera recomeça a contar da nova abertura
    clock.now = 60.0
    assert breaker.allow()
