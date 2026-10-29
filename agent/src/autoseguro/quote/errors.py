"""Classificação das respostas e erros da API de cotação.

Decide, para cada resposta, se vale tentar de novo. A tabela completa e o porquê de cada
linha estão em docs/entregas/B1-cotacao-resiliente.md.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

import httpx


class ErrorKind(StrEnum):
    TIMEOUT = "timeout"
    CONNECTION = "connection"
    SERVER_ERROR = "server_error"
    RATE_LIMITED = "rate_limited"
    BAD_RESPONSE = "bad_response"
    REFUSED = "refused"
    INVALID_PAYLOAD = "invalid_payload"
    CLIENT_ERROR = "client_error"


RETRYABLE: frozenset[ErrorKind] = frozenset(
    {
        ErrorKind.TIMEOUT,
        ErrorKind.CONNECTION,
        ErrorKind.SERVER_ERROR,
        ErrorKind.RATE_LIMITED,
        ErrorKind.BAD_RESPONSE,
    }
)


def classify_response(status_code: int, body: Any) -> ErrorKind | None:
    """`None` para 200 (o corpo ainda precisa validar); senão o tipo de erro.

    O 422 tem dois significados nessa API: recusa de negócio (`error=cotacao_recusada`)
    ou validação do FastAPI (`{"detail": [...]}`). Por isso o corpo decide, não o status.
    """
    if status_code == 200:
        return None
    if status_code == 422:
        if isinstance(body, dict) and body.get("error") == "cotacao_recusada":
            motivo = str(body.get("motivo", "")).lower()
            # Plano inexistente não é recusa: plano_id é um enum do nosso lado, então é bug.
            if motivo.startswith("plano") and "inexistente" in motivo:
                return ErrorKind.INVALID_PAYLOAD
            return ErrorKind.REFUSED
        return ErrorKind.INVALID_PAYLOAD
    if status_code == 400:
        return ErrorKind.INVALID_PAYLOAD
    if status_code == 429:
        return ErrorKind.RATE_LIMITED
    if 500 <= status_code <= 599:
        return ErrorKind.SERVER_ERROR
    if 400 <= status_code <= 499:
        return ErrorKind.CLIENT_ERROR
    return ErrorKind.BAD_RESPONSE


def classify_exception(exc: BaseException) -> ErrorKind:
    if isinstance(exc, (TimeoutError, httpx.TimeoutException)):
        return ErrorKind.TIMEOUT
    if isinstance(exc, httpx.TransportError):
        return ErrorKind.CONNECTION
    raise exc
