"""Cache em memória das respostas determinísticas da API de cotação.

A cotação é uma função pura do pedido **e da data de hoje** (a API calcula a idade do
veículo com a data atual), então a data entra na chave. Só guardamos sucesso e recusa:
falhas nunca vão para o cache.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

from autoseguro.domain.models import QuoteRequest, QuoteResult


def cache_key(req: QuoteRequest, today: date) -> str:
    canonical = json.dumps(req.to_api_payload(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(f"{canonical}|{today.isoformat()}".encode()).hexdigest()


@dataclass(frozen=True)
class CachedSuccess:
    result: QuoteResult


@dataclass(frozen=True)
class CachedRefusal:
    motivo: str


CachedAnswer = CachedSuccess | CachedRefusal


class QuoteCache:
    def __init__(self, *, ttl_s: float = 900.0, clock: Callable[[], float] = time.monotonic):
        self._ttl_s = ttl_s
        self._clock = clock
        self._entries: dict[str, tuple[float, CachedAnswer]] = {}

    def get(self, key: str) -> CachedAnswer | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        expires_at, answer = entry
        if self._clock() >= expires_at:
            del self._entries[key]
            return None
        return answer

    def put(self, key: str, answer: CachedAnswer) -> None:
        self._entries[key] = (self._clock() + self._ttl_s, answer)
