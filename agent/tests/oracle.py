"""Oráculo de preço: reimplementação das regras de `quote-service/data/plans.json`.

**Só para testes e avaliação.** Serve para conferir se o que a API devolve (e o que o agente
mostra) está certo. Nunca é usado para responder ao lead: preço exibido vem sempre da API.
`tests/unit/test_architecture.py` garante que nada em `src/` importa este módulo.

Usa a mesma aritmética da API (multiplicação em float e `round(x, 2)`), para bater centavo
a centavo, e recebe a data de referência por parâmetro.
"""

from __future__ import annotations

import json
from calendar import monthrange
from dataclasses import dataclass
from datetime import date
from functools import cache
from pathlib import Path
from typing import Any

PLANS_PATH = Path(__file__).resolve().parents[2] / "quote-service" / "data" / "plans.json"


@cache
def load_plans() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(PLANS_PATH.read_text(encoding="utf-8"))
    return data


@dataclass(frozen=True)
class OracleQuote:
    premio_mensal: float
    franquia: float
    valor_primeiro_pagamento: float | None


@dataclass(frozen=True)
class OracleRefusal:
    motivo: str


def _band_multiplier(
    bands: list[dict[str, Any]], value: int, lo: str, hi: str, fallback: str
) -> float | OracleRefusal:
    for band in bands:
        if band[lo] <= value <= band[hi]:
            if band.get("recusar"):
                return OracleRefusal(band["motivo"])
            return float(band["multiplicador"])
    return OracleRefusal(fallback)


def expected_quote(
    *,
    plano_id: str,
    idade: int,
    veiculo_ano: int,
    cep: str | None,
    data_inicio: date | None,
    today: date,
) -> OracleQuote | OracleRefusal:
    plans = load_plans()
    rules = plans["regras"]
    plano = next(p for p in plans["planos"] if p["id"] == plano_id)

    m_idade = _band_multiplier(
        rules["faixa_etaria"], idade, "idade_min", "idade_max", "Idade fora das faixas aceitas."
    )
    if isinstance(m_idade, OracleRefusal):
        return m_idade
    m_veiculo = _band_multiplier(
        rules["idade_veiculo"],
        today.year - veiculo_ano,
        "anos_min",
        "anos_max",
        "Idade do veiculo fora das faixas aceitas.",
    )
    if isinstance(m_veiculo, OracleRefusal):
        return m_veiculo
    regiao = rules["regiao_cep"]
    prefixo = cep.replace("-", "").strip()[:2] if cep else None
    m_regiao = regiao["multiplicador"] if prefixo in regiao["prefixos_alto_risco"] else 1.0

    premio = round(plano["base_mensal"] * m_idade * m_veiculo * m_regiao, 2)

    pro_rata = None
    if data_inicio and data_inicio.day != 1:
        dias_mes = monthrange(data_inicio.year, data_inicio.month)[1]
        dias = dias_mes - data_inicio.day + 1
        pro_rata = round(premio * dias / dias_mes, 2)

    return OracleQuote(premio, float(plano["franquia"]), pro_rata)
