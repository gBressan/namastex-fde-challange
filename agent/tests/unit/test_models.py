from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from autoseguro.domain.handoff import HandoffReason
from autoseguro.domain.models import LeadSlots, PlanId, QuoteRequest, QuoteResult

# Resposta da API (regras de plans.json): completo, 35 anos, 2022, CEP 01310-100, início 15/07.
API_RESPONSE = {
    "plano_id": "completo",
    "plano_nome": "Completo",
    "premio_mensal": 209.9,
    "franquia": 3000,
    "coberturas": ["colisao", "roubo", "furto", "terceiros", "vidros"],
    "multiplicadores": {"faixa_etaria": 1.0, "idade_veiculo": 1.0, "regiao": 1.0},
    "carencia": {
        "coberturas": ["roubo", "furto"],
        "dias": 30,
        "observacao": "Coberturas de roubo e furto so passam a valer apos a carencia.",
    },
    "moeda": "BRL",
    "primeiro_pagamento_pro_rata": {
        "dias_no_mes": 31,
        "dias_cobrados": 17,
        "valor_primeiro_pagamento": 115.11,
    },
}


def test_lead_slots_missing_respeita_a_ordem_das_perguntas() -> None:
    assert LeadSlots().missing() == ["veiculo_ano", "idade", "cep", "plano_id", "data_inicio"]
    slots = LeadSlots(veiculo_ano=2022, cep="01310100")
    assert slots.missing() == ["idade", "plano_id", "data_inicio"]


def test_lead_slots_rejeita_cep_fora_do_formato() -> None:
    with pytest.raises(ValidationError):
        LeadSlots(cep="01310-100")


def test_to_quote_request_exige_dados_completos() -> None:
    with pytest.raises(ValueError, match="incompletos"):
        LeadSlots(idade=35).to_quote_request()


def test_quote_request_payload_no_formato_da_api() -> None:
    slots = LeadSlots(
        veiculo_descricao="Onix 2022",
        veiculo_ano=2022,
        idade=35,
        cep="01310100",
        plano_id=PlanId.COMPLETO,
        data_inicio=date(2026, 11, 15),
    )
    assert slots.to_quote_request().to_api_payload() == {
        "plano_id": "completo",
        "idade": 35,
        "veiculo_ano": 2022,
        "cep": "01310-100",
        "data_inicio": "2026-11-15",
    }


def test_quote_request_e_imutavel() -> None:
    req = QuoteRequest(
        plano_id=PlanId.ESSENCIAL, idade=40, veiculo_ano=2020, cep="21310100",
        data_inicio=date(2026, 11, 1),
    )  # fmt: skip
    with pytest.raises(ValidationError):
        req.idade = 41  # type: ignore[misc]


def test_quote_result_converte_valores_para_decimal_sem_ruido_de_float() -> None:
    result = QuoteResult.model_validate(API_RESPONSE)
    assert result.premio_mensal == Decimal("209.9")
    assert result.franquia == Decimal("3000")
    assert result.primeiro_pagamento_pro_rata is not None
    assert result.primeiro_pagamento_pro_rata.valor_primeiro_pagamento == Decimal("115.11")


def test_quote_result_sem_pro_rata() -> None:
    data = {k: v for k, v in API_RESPONSE.items() if k != "primeiro_pagamento_pro_rata"}
    assert QuoteResult.model_validate(data).primeiro_pagamento_pro_rata is None


def test_quote_result_invalido_falha_na_validacao() -> None:
    with pytest.raises(ValidationError):
        QuoteResult.model_validate({"error": "upstream_unavailable"})


def test_todos_os_motivos_de_handoff_tem_descricao() -> None:
    assert len(HandoffReason) == 10
    assert all(reason.description for reason in HandoffReason)
