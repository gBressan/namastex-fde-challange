"""Formato da saída estruturada que o LLM devolve a cada mensagem do lead."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from autoseguro.domain.models import PlanId


class Intent(StrEnum):
    INFORMAR_DADOS = "informar_dados"
    CONFIRMAR = "confirmar"
    CORRIGIR = "corrigir"
    PEDIR_HUMANO = "pedir_humano"
    ACEITAR_PROPOSTA = "aceitar_proposta"
    NEGOCIAR_PRECO = "negociar_preco"
    OBJECAO_FRANQUIA = "objecao_franquia"
    OBJECAO_PENSAR = "objecao_pensar"
    DUVIDA = "duvida"
    FORA_ESCOPO = "fora_escopo"
    SAUDACAO = "saudacao"
    DESPEDIDA = "despedida"
    OUTRO = "outro"


class Extraction(BaseModel):
    """O que o LLM entendeu da(s) mensagem(ns) do lead.

    Só extrai: normalizar e validar é trabalho do código (`domain.normalizers`). A data,
    por exemplo, volta como o texto que o lead escreveu.
    """

    intent: Intent = Field(description="Intenção principal da última mensagem do lead.")
    veiculo_descricao: str | None = Field(
        default=None, description="Marca e modelo do veículo, como o lead escreveu. Ex.: 'Onix'."
    )
    veiculo_ano: int | None = Field(
        default=None, description="Ano do veículo com 4 dígitos, se o lead informou."
    )
    idade: int | None = Field(default=None, description="Idade do condutor em anos.")
    cep: str | None = Field(
        default=None, description="CEP onde o carro dorme, como escrito. Ex.: '01310-100'."
    )
    plano_id: PlanId | None = Field(
        default=None,
        description="Plano escolhido, se o lead disse: essencial, completo ou premium.",
    )
    data_inicio_texto: str | None = Field(
        default=None,
        description="Quando o lead quer começar o seguro, no texto dele. Ex.: 'amanhã', '15/11'.",
    )
    menciona_concorrente: bool = Field(
        default=False, description="Se o lead citou outra seguradora ou preço de concorrente."
    )
    confidence: float = Field(
        ge=0, le=1, description="Confiança de 0 a 1 na intenção e nos dados extraídos."
    )
