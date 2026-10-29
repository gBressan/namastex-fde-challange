"""Motivos de passagem para um humano.

A ordem e a forma de detecção de cada um estão em docs/entregas/B4-handoff-e-degradacao.md.
"""

from __future__ import annotations

from enum import StrEnum


class HandoffReason(StrEnum):
    USER_REQUESTED = "USER_REQUESTED"
    SAFETY = "SAFETY"
    READY_TO_CLOSE = "READY_TO_CLOSE"
    PRICE_NEGOTIATION = "PRICE_NEGOTIATION"
    QUOTE_UNAVAILABLE = "QUOTE_UNAVAILABLE"
    UNDERWRITING_REFUSAL = "UNDERWRITING_REFUSAL"
    EDGE_CASE = "EDGE_CASE"
    MEDIA_RECEIVED = "MEDIA_RECEIVED"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"

    @property
    def description(self) -> str:
        return _DESCRIPTIONS[self]


_DESCRIPTIONS: dict[HandoffReason, str] = {
    HandoffReason.USER_REQUESTED: "Lead pediu para falar com uma pessoa",
    HandoffReason.SAFETY: "Hostilidade ou tentativa de manipular o agente",
    HandoffReason.READY_TO_CLOSE: "Lead aceitou a proposta; emissão é feita por humano",
    HandoffReason.PRICE_NEGOTIATION: "Negociação de preço ou comparação com concorrente",
    HandoffReason.QUOTE_UNAVAILABLE: "API de cotação indisponível até o fim do prazo",
    HandoffReason.UNDERWRITING_REFUSAL: "Perfil recusado pelas regras de aceitação",
    HandoffReason.EDGE_CASE: "Caso de borda que o agente não resolve com segurança",
    HandoffReason.MEDIA_RECEIVED: "Lead enviou mídia que o agente não processa",
    HandoffReason.OUT_OF_SCOPE: "Assunto fora de cotação de seguro auto",
    HandoffReason.LOW_CONFIDENCE: "Agente não conseguiu entender o lead",
}
