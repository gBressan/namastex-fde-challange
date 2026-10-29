"""Resultados possíveis de uma cotação. O cliente sempre devolve um destes, nunca lança
exceção por falha da API: quem chama trata cada caso explicitamente."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from autoseguro.domain.models import QuoteResult

UnavailableReason = Literal["retries_exhausted", "deadline", "circuit_open"]


@dataclass(frozen=True)
class QuoteSucceeded:
    quote_id: UUID
    result: QuoteResult
    attempts: int
    from_cache: bool = False


@dataclass(frozen=True)
class QuoteRefused:
    """422 `cotacao_recusada`: o perfil não passa nas regras de aceitação."""

    quote_id: UUID
    motivo: str
    attempts: int
    from_cache: bool = False


@dataclass(frozen=True)
class QuoteInvalid:
    """400, 422 de validação ou outro 4xx: o pedido que montamos está errado (bug nosso)."""

    quote_id: UUID
    detalhe: str
    attempts: int


@dataclass(frozen=True)
class QuoteUnavailable:
    """A API não respondeu a tempo. Nunca vira preço: o agente avisa e tenta depois."""

    quote_id: UUID
    reason: UnavailableReason
    attempts: int
    elapsed_ms: int


QuoteOutcome = QuoteSucceeded | QuoteRefused | QuoteInvalid | QuoteUnavailable
