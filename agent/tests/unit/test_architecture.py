"""Regras de arquitetura verificadas por teste."""

from __future__ import annotations

import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"


def test_codigo_de_producao_nao_usa_o_oraculo_de_preco() -> None:
    """O preço mostrado ao lead vem sempre da API. O oráculo é só para testes."""
    offenders = [
        str(path.relative_to(SRC))
        for path in SRC.rglob("*.py")
        if re.search(r"^\s*(from|import)\s+\S*oracle", path.read_text(encoding="utf-8"), re.M)
    ]
    assert offenders == []


def test_dominio_nao_faz_io() -> None:
    domain = SRC / "autoseguro" / "domain"
    forbidden = re.compile(r"^\s*(from|import)\s+(httpx|psycopg|sqlalchemy|langgraph)", re.M)
    offenders = [
        path.name for path in domain.rglob("*.py") if forbidden.search(path.read_text("utf-8"))
    ]
    assert offenders == []
