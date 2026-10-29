"""Circuit breaker em memória (por processo).

Fechado: as chamadas passam e as falhas consecutivas são contadas.
Aberto: depois de `failure_threshold` falhas seguidas, nenhuma chamada sai até passar
`reset_timeout_s`.
Meio-aberto: passado esse tempo, uma única chamada de teste sai. Se der certo, fecha;
se falhar, abre de novo.

Só falhas transitórias contam. Uma recusa (422) ou um 400 são respostas da API: provam
que ela está de pé.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from enum import StrEnum


class BreakerState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    def __init__(
        self,
        *,
        failure_threshold: int = 5,
        reset_timeout_s: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._threshold = failure_threshold
        self._reset_timeout_s = reset_timeout_s
        self._clock = clock
        self._state = BreakerState.CLOSED
        self._failures = 0
        self._opened_at = 0.0

    @property
    def state(self) -> BreakerState:
        return self._state

    def allow(self) -> bool:
        """Pode chamar a API agora? No estado aberto, libera uma chamada de teste quando o
        tempo de espera acaba (e passa para meio-aberto)."""
        if self._state is BreakerState.CLOSED:
            return True
        if self._state is BreakerState.OPEN:
            if self._clock() - self._opened_at >= self._reset_timeout_s:
                self._state = BreakerState.HALF_OPEN
                return True
            return False
        # Meio-aberto: a chamada de teste já saiu; as outras esperam o resultado dela.
        return False

    def record_success(self) -> None:
        self._failures = 0
        self._state = BreakerState.CLOSED

    def record_failure(self) -> None:
        if self._state is BreakerState.HALF_OPEN:
            self._open()
            return
        self._failures += 1
        if self._failures >= self._threshold:
            self._open()

    def _open(self) -> None:
        self._state = BreakerState.OPEN
        self._opened_at = self._clock()
        self._failures = 0
