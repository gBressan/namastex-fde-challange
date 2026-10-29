"""Modelos do domínio: dados do lead, pedido e resultado de cotação, estados da conversa."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from autoseguro.domain.normalizers import format_cep


class PlanId(StrEnum):
    ESSENCIAL = "essencial"
    COMPLETO = "completo"
    PREMIUM = "premium"


class ConversationStatus(StrEnum):
    SAUDACAO = "saudacao"
    COLETANDO = "coletando"
    CONFIRMANDO = "confirmando"
    COTANDO = "cotando"
    COTACAO_PENDENTE = "cotacao_pendente"
    COTADO = "cotado"
    HANDOFF = "handoff"
    ENCERRADO = "encerrado"


# Ordem em que o agente pergunta os dados que faltam.
SLOT_ORDER: tuple[str, ...] = ("veiculo_ano", "idade", "cep", "plano_id", "data_inicio")


class LeadSlots(BaseModel):
    """Dados coletados ao longo da conversa. Tudo opcional até a confirmação.

    Só guarda o necessário para cotar (minimização): nada de CPF, e-mail, telefone ou placa.
    """

    veiculo_descricao: str | None = None
    veiculo_ano: int | None = None
    idade: int | None = None
    cep: str | None = Field(default=None, pattern=r"^\d{8}$")
    plano_id: PlanId | None = None
    data_inicio: date | None = None

    def missing(self) -> list[str]:
        return [slot for slot in SLOT_ORDER if getattr(self, slot) is None]

    def is_complete(self) -> bool:
        return not self.missing()

    def to_quote_request(self) -> QuoteRequest:
        if not self.is_complete():
            raise ValueError(f"Dados incompletos para cotar: {self.missing()}")
        return QuoteRequest.model_validate(self.model_dump(exclude={"veiculo_descricao"}))


class QuoteRequest(BaseModel):
    """Pedido de cotação completo e normalizado.

    Valida só o formato. As regras de aceitação (idade 18-75, veículo até 20 anos) ficam
    na API, que é a fonte da verdade.
    """

    model_config = ConfigDict(frozen=True)

    plano_id: PlanId
    idade: int = Field(ge=0, le=200)
    veiculo_ano: int = Field(ge=1950, le=2100)
    cep: str = Field(pattern=r"^\d{8}$")
    data_inicio: date

    def to_api_payload(self) -> dict[str, Any]:
        return {
            "plano_id": self.plano_id.value,
            "idade": self.idade,
            "veiculo_ano": self.veiculo_ano,
            "cep": format_cep(self.cep),
            "data_inicio": self.data_inicio.isoformat(),
        }


class Multiplicadores(BaseModel):
    faixa_etaria: float
    idade_veiculo: float
    regiao: float


class Carencia(BaseModel):
    coberturas: list[str]
    dias: int
    observacao: str


class ProRata(BaseModel):
    dias_no_mes: int
    dias_cobrados: int
    valor_primeiro_pagamento: Decimal


class QuoteResult(BaseModel):
    """Resposta 200 da `POST /quote`. Valores monetários sempre em `Decimal`."""

    model_config = ConfigDict(frozen=True)

    plano_id: PlanId
    plano_nome: str
    premio_mensal: Decimal
    franquia: Decimal
    coberturas: list[str]
    multiplicadores: Multiplicadores
    carencia: Carencia
    moeda: str
    primeiro_pagamento_pro_rata: ProRata | None = None

    @field_validator("premio_mensal", "franquia", mode="before")
    @classmethod
    def _float_to_decimal(cls, value: Any) -> Any:
        # A API devolve float (ex.: 262.38). Converter via str evita 262.37999999...
        if isinstance(value, float):
            return Decimal(str(value))
        return value
