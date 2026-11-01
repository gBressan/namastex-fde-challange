"""Camada Silver gerada a partir do dataset real (dataset/conversations.parquet)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import duckdb
import pytest

from autoseguro.data.silver import DEFAULT_DATASET, build_silver, find_leaks
from autoseguro.safety.pii import PIIType

pytestmark = pytest.mark.data


@pytest.fixture(scope="module")
def silver(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("silver") / "lead_messages.parquet"
    build_silver(DEFAULT_DATASET, out, salt="teste")
    return out


def _rows(silver: Path, sql: str) -> list[tuple[Any, ...]]:
    con = duckdb.connect()
    try:
        return con.execute(sql.replace("SILVER", "read_parquet(?)"), [str(silver)]).fetchall()
    finally:
        con.close()


def test_nenhum_dado_pessoal_escapa(silver: Path) -> None:
    assert find_leaks(silver) == []


def test_checagem_independente_sem_regex_do_redator(silver: Path) -> None:
    # Padrões simples e diferentes dos do redator, para não validar o código com ele mesmo.
    bodies = [str(r[0]) for r in _rows(silver, "SELECT body_redacted FROM SILVER")]
    text = "\n".join(bodies)
    assert "@" not in text
    assert not re.search(r"\d{3}\.\d{3}\.\d{3}-\d{2}", text)
    assert not re.search(r"\+55", text)
    assert not re.search(r"\b[A-Z]{3}\d[A-Z0-9]\d{2}\b", text)


def test_contagens_batem_com_o_dataset(tmp_path: Path) -> None:
    stats = build_silver(DEFAULT_DATASET, tmp_path / "s.parquet", salt="teste")
    con = duckdb.connect()
    try:
        source = con.execute(
            "SELECT count(*) FILTER (WHERE message_body LIKE '%@%'),"
            " count(*) FILTER (WHERE message_body ILIKE '%placa%')"
            " FROM read_parquet(?) WHERE sender_role = 'lead'",
            [str(DEFAULT_DATASET)],
        ).fetchone()
    finally:
        con.close()
    assert source is not None
    emails, placas = source

    assert stats.conversations == 2500
    assert stats.masked[PIIType.CPF] == 2500  # todo lead manda CPF uma vez
    assert stats.masked[PIIType.EMAIL] == emails
    assert stats.masked[PIIType.TELEFONE] == emails  # e-mail e telefone vêm juntos
    assert stats.masked[PIIType.PLACA] == placas


def test_so_leads_em_ordem_de_message_index(silver: Path) -> None:
    rows = _rows(silver, "SELECT conversation_id, message_index FROM SILVER")
    assert rows == sorted(rows)
    vendedor = _rows(silver, "SELECT count(*) FROM SILVER WHERE sender_pseudonym NOT LIKE 'lead_%'")
    assert vendedor == [(0,)]


def test_gabarito_completo(silver: Path) -> None:
    [(sem_idade, sem_ano, sem_cep, prefixos)] = _rows(
        silver,
        "SELECT count(*) FILTER (WHERE gt_idade IS NULL),"
        " count(*) FILTER (WHERE gt_veiculo_ano IS NULL),"
        " count(*) FILTER (WHERE gt_cep_prefixo IS NULL),"
        " count(DISTINCT gt_cep_prefixo) FROM SILVER",
    )
    assert (sem_idade, sem_ano, sem_cep) == (0, 0, 0)
    assert prefixos == 14  # os 14 prefixos que o gerador usa


def test_pseudonimo_e_estavel_e_nao_revela_o_nome(silver: Path) -> None:
    [(conversas, pseudonimos)] = _rows(
        silver,
        "SELECT count(DISTINCT conversation_id), count(DISTINCT sender_pseudonym) FROM SILVER",
    )
    assert pseudonimos <= conversas  # um por lead (nomes podem repetir entre conversas)
    [(amostra,)] = _rows(silver, "SELECT sender_pseudonym FROM SILVER LIMIT 1")
    assert re.fullmatch(r"lead_[0-9a-f]{8}", str(amostra))
