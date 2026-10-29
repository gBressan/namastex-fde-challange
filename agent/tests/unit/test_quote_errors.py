from __future__ import annotations

from typing import Any

import httpx
import pytest

from autoseguro.quote.errors import RETRYABLE, ErrorKind, classify_exception, classify_response


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (200, {}, None),
        (500, {"error": "upstream_unavailable"}, ErrorKind.SERVER_ERROR),
        (502, {"error": "upstream_unavailable"}, ErrorKind.SERVER_ERROR),
        (503, {"error": "upstream_unavailable"}, ErrorKind.SERVER_ERROR),
        (504, None, ErrorKind.SERVER_ERROR),
        (429, None, ErrorKind.RATE_LIMITED),
        (
            422,
            {"error": "cotacao_recusada", "motivo": "Idade acima do limite de aceitacao."},
            ErrorKind.REFUSED,
        ),
        (
            422,
            {"error": "cotacao_recusada", "motivo": "Idade do veiculo fora das faixas aceitas."},
            ErrorKind.REFUSED,
        ),
        (
            422,
            {"error": "cotacao_recusada", "motivo": "Plano 'ouro' inexistente. Opcoes: essencial"},
            ErrorKind.INVALID_PAYLOAD,
        ),
        (
            422,
            {"detail": [{"loc": ["body", "idade"], "msg": "Field required"}]},
            ErrorKind.INVALID_PAYLOAD,
        ),
        (422, None, ErrorKind.INVALID_PAYLOAD),
        (
            400,
            {"error": "payload_invalido", "detalhe": "Invalid isoformat"},
            ErrorKind.INVALID_PAYLOAD,
        ),
        (404, None, ErrorKind.CLIENT_ERROR),
        (302, None, ErrorKind.BAD_RESPONSE),
    ],
)
def test_classify_response(status: int, body: Any, expected: ErrorKind | None) -> None:
    assert classify_response(status, body) == expected


def test_classify_exception() -> None:
    assert classify_exception(httpx.ReadTimeout("x")) is ErrorKind.TIMEOUT
    assert classify_exception(httpx.ConnectTimeout("x")) is ErrorKind.TIMEOUT
    assert classify_exception(TimeoutError()) is ErrorKind.TIMEOUT
    assert classify_exception(httpx.ConnectError("x")) is ErrorKind.CONNECTION
    assert classify_exception(httpx.RemoteProtocolError("x")) is ErrorKind.CONNECTION
    with pytest.raises(KeyError):
        classify_exception(KeyError("bug nosso"))


def test_so_falhas_transitorias_permitem_retry() -> None:
    assert ErrorKind.REFUSED not in RETRYABLE
    assert ErrorKind.INVALID_PAYLOAD not in RETRYABLE
    assert ErrorKind.CLIENT_ERROR not in RETRYABLE
    assert {ErrorKind.TIMEOUT, ErrorKind.SERVER_ERROR, ErrorKind.CONNECTION} <= RETRYABLE
